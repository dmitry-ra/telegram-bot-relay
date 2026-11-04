import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
import uvicorn
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetUpdates
from pydantic import JsonValue

from telegram_bot_relay import cli
from telegram_bot_relay.config import Gateway
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import Service
from tests.helpers import FakeClient, configuration, message


def test_poll_retries_and_commit_before_next_request(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = FakeClient()
        service = Service(config, journal, client)
        method = GetUpdates()
        calls = 0

        async def updates(offset: int | None) -> list[dict[str, JsonValue]]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TelegramRetryAfter(method, "limited", 1)
            if calls == 2:
                raise TelegramNetworkError(method, "unavailable")
            if calls == 3:
                return [message()]
            assert offset == 2
            assert list(journal.records())[-1].type == "message_received"
            raise TelegramConflictError(method, "conflict")

        with (
            patch.object(client, "updates", side_effect=updates),
            patch("telegram_bot_relay.service.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="Another"):
                await service.poll()
        journal.close()

    asyncio.run(run())


def test_background_workers_and_cancellation(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        service = Service(config, journal, FakeClient())
        with (
            patch.object(service, "process_once", new=AsyncMock(return_value=False)),
            patch(
                "telegram_bot_relay.service.asyncio.sleep",
                new=AsyncMock(side_effect=asyncio.CancelledError),
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await service.processing()
        with (
            patch.object(
                service,
                "forward_once",
                new=AsyncMock(side_effect=[aiohttp.ClientConnectionError(), False]),
            ),
            patch(
                "telegram_bot_relay.service.asyncio.sleep",
                new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await service.forwarding()
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(service, "poll", new=AsyncMock(side_effect=RuntimeError("stop"))),
            patch.object(service, "processing", new=AsyncMock()),
        ):
            with pytest.raises(ExceptionGroup):
                await service.run()
            assert service.bot_lock is not None and service.bot_lock.closed
        config.gateway = Gateway(url="http://localhost", target="work", start="oldest")
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(service, "poll", new=AsyncMock(side_effect=RuntimeError("stop"))),
            patch.object(service, "processing", new=AsyncMock()),
            patch.object(service, "forwarding", new=AsyncMock()),
        ):
            with pytest.raises(ExceptionGroup):
                await service.run()
        journal.close()

    asyncio.run(run())


def test_cli_lifecycle_closes_resources(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    Path(config.token_file).write_text("123456:TEST_TOKEN")
    Path(config.api_token_file).write_text("api-secret")
    client = FakeClient()
    with (
        patch.object(Path, "home", return_value=tmp_path),
        patch.object(cli, "Client", return_value=client),
        patch.object(Service, "run", new=AsyncMock()),
        patch.object(uvicorn.Server, "serve", new=AsyncMock()),
    ):
        asyncio.run(cli.serve(config))
    # A second writer can acquire the archive only after the first closed.
    journal = Journal(Path(config.data_dir))
    journal.close()


def test_cli_errors_are_redacted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "config.toml"
    with patch("sys.argv", ["telegram-bot-relay", "--config", str(path)]):
        with pytest.raises(SystemExit) as error:
            cli.main()
    assert error.value.code == 1
    assert "FileNotFoundError" in capsys.readouterr().err
    with patch("sys.argv", ["telegram-bot-relay", "--version"]):
        with pytest.raises(SystemExit) as error:
            cli.main()
    assert error.value.code == 0


def test_actual_http_daemon_shutdown(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        Path(config.token_file).write_text("123456:TEST_TOKEN")
        Path(config.api_token_file).write_text("api-secret")
        original_server = uvicorn.Server
        servers: list[uvicorn.Server] = []

        def server_factory(settings: uvicorn.Config) -> uvicorn.Server:
            settings.port = 0
            server = original_server(settings)
            servers.append(server)
            return server

        async def pending() -> None:
            await asyncio.Event().wait()

        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(cli, "Client", return_value=FakeClient()),
            patch.object(Service, "run", side_effect=pending),
            patch.object(uvicorn, "Server", side_effect=server_factory),
        ):
            task = asyncio.create_task(cli.serve(config))
            async with asyncio.timeout(10):
                while not servers or not servers[0].started:
                    await asyncio.sleep(0.01)
                servers[0].should_exit = True
                await task
        journal = Journal(Path(config.data_dir))
        journal.close()

    asyncio.run(run())
