import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from telegram_bot_relay.application.models import Media, SendMedia
from telegram_bot_relay.application.operations import Operations
from telegram_bot_relay.config import Gateway
from telegram_bot_relay.journal.reader import continuation
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import Service
from tests.helpers import FakeClient, configuration, message


def test_archive_identity_cannot_be_reused_by_another_bot(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    journal = Journal(Path(config.data_dir))
    journal.append("bot_started", {"bot_id": 999})
    service = Service(config, journal, FakeClient())
    with pytest.raises(ValueError, match="different bot"):
        asyncio.run(service.run())
    journal.close()


def test_unfinished_operation_becomes_uncertain_without_resending(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    journal = Journal(Path(config.data_dir))
    journal.append("operation_requested", {"operation_id": "unresolved", "request": {}})
    journal.close()
    journal = Journal(Path(config.data_dir))
    client = FakeClient()
    Operations(config, journal, client)
    assert list(journal.records())[-1].data["status"] == "uncertain"
    assert not client.sent
    count = journal.seq
    Operations(config, journal, client)
    assert journal.seq == count
    journal.close()


def test_outbound_not_attempted_without_durable_request(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = FakeClient()
        operations = Operations(config, journal, client)
        with patch.object(journal, "append", side_effect=OSError("full")):
            with pytest.raises(OSError):
                await operations.execute(
                    SendMedia(chat_id=101, media=[Media(kind="photo", file_id="existing")]), "mcp"
                )
        assert not client.sent
        journal.close()

    asyncio.run(run())


def test_latest_gateway_start_skips_old_unprepared_input(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config.gateway = Gateway(url="http://127.0.0.1:1", target="work", start="latest")
        journal = Journal(Path(config.data_dir))
        service = Service(config, journal, FakeClient())
        service.ingest(message())
        assert not await service.forward_once()
        assert await service.process_once()
        assert not await service.forward_once()
        journal.close()

    asyncio.run(run())


def test_continuation_rejects_unsafe_paths(tmp_path: Path) -> None:
    journal = Journal(tmp_path)
    path = tmp_path / journal.period / "events.jsonl"
    journal._append("rotate", {"next_events": "../../outside"})
    with pytest.raises(ValueError, match="escapes"):
        continuation(path)
    journal.close()
    with pytest.raises(ValueError):
        Journal(tmp_path)
