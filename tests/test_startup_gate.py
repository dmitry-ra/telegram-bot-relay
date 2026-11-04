import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
import uvicorn

from telegram_bot_relay import cli
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import Service
from tests.helpers import FakeClient, configuration


@pytest.mark.parametrize("failure", ["identity", "archive", "duplicate"])
def test_http_is_not_constructed_before_startup_validation(tmp_path: Path, failure: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        Path(config.token_file).write_text("123456:TEST_TOKEN")
        Path(config.api_token_file).write_text("api-secret")
        journal = Journal(Path(config.data_dir))
        if failure == "archive":
            journal.append("bot_started", {"bot_id": 999})
        journal.close()
        client = FakeClient()
        held: Service | None = None
        with patch.object(Path, "home", return_value=tmp_path):
            if failure == "duplicate":
                held_journal = Journal(tmp_path / "held")
                held = Service(configuration(tmp_path / "held-config"), held_journal, FakeClient())
                await held.initialize()
            try:
                with (
                    patch.object(cli, "Client", return_value=client),
                    patch.object(uvicorn, "Server") as server,
                ):
                    if failure == "identity":
                        with patch.object(
                            client, "identity", side_effect=RuntimeError("identity failed")
                        ):
                            with pytest.raises(RuntimeError):
                                await cli.serve(config)
                    else:
                        with pytest.raises((ValueError, BlockingIOError)):
                            await cli.serve(config)
                    server.assert_not_called()
                reopened = Journal(Path(config.data_dir))
                reopened.close()
            finally:
                if held is not None:
                    held.close()
                    held_journal.close()

    asyncio.run(run())


def test_initialization_is_idempotent_and_holds_lock_until_close(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        service = Service(config, journal, FakeClient())
        with patch.object(Path, "home", return_value=tmp_path):
            await service.initialize()
            await service.initialize()
        assert len([r for r in journal.records() if r.type == "bot_started"]) == 1
        assert service.bot_lock is not None and not service.bot_lock.closed
        service.close()
        assert service.bot_lock.closed
        journal.close()

    asyncio.run(run())
