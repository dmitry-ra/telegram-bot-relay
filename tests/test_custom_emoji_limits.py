"""Inspection budgets bound a whole request while preserving available metadata."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from pydantic import JsonValue

from telegram_bot_relay.application.custom_emoji import InspectCustomEmoji, inspect_custom_emoji
from telegram_bot_relay.journal.writer import Journal
from tests.helpers import FakeClient, configuration


class ThumbnailClient(FakeClient):
    lookup_delay = 0.0
    download_delay = 0.0

    async def custom_emoji(self, ids: list[str]) -> list[JsonValue]:
        await asyncio.sleep(self.lookup_delay)
        return [
            {"custom_emoji_id": value, "file_id": value, "thumbnail": {"file_id": "thumb" + value}}
            for value in ids
        ]

    async def download(self, file_id: str, path: Path, limit: int, timeout: int) -> None:
        self.downloaded.append(file_id)
        await asyncio.sleep(self.download_delay)
        data = b"example file"
        if len(data) > limit:
            raise ValueError("size limit")
        path.write_bytes(data)


def test_thumbnail_byte_budget_is_shared_by_all_requested_ids(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config.attachments.max_bytes = len(b"example file")
        journal = Journal(Path(config.data_dir))
        client = ThumbnailClient()
        result = await inspect_custom_emoji(
            InspectCustomEmoji(custom_emoji_ids=["1", "2", "3"], download_thumbnail=True),
            config,
            journal,
            client,
            "test",
        )
        assert result.status == "ok" and len(result.stickers) == 3
        assert client.downloaded == ["thumb1"]
        downloads = [item["thumbnail_download"] for item in result.stickers]
        assert isinstance(downloads[0], dict) and downloads[0]["status"] == "saved"
        assert downloads[1:] == [{"status": "failed", "error": "ValueError"}] * 2
        saved = list(journal.root.glob("*/inspections/*/*.webp"))
        assert sum(path.stat().st_size for path in saved) == config.attachments.max_bytes
        journal.close()

    asyncio.run(run())


def test_thumbnail_deadline_includes_all_downloads(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config.attachments.timeout = 1
        journal = Journal(Path(config.data_dir))
        client = ThumbnailClient()
        client.download_delay = 0.6
        result = await inspect_custom_emoji(
            InspectCustomEmoji(custom_emoji_ids=["1", "2", "3"], download_thumbnail=True),
            config,
            journal,
            client,
            "test",
        )
        assert result.status == "ok" and len(result.stickers) == 3
        assert client.downloaded == ["thumb1", "thumb2"]
        downloads = [item["thumbnail_download"] for item in result.stickers]
        assert isinstance(downloads[0], dict) and downloads[0]["status"] == "saved"
        assert downloads[1:] == [{"status": "failed", "error": "TimeoutError"}] * 2
        assert len(list(journal.root.glob("*/inspections/*/*.webp"))) == 1
        journal.close()

    asyncio.run(run())


def test_metadata_lookup_has_its_own_short_timeout(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = ThumbnailClient()
        client.lookup_delay = 60
        with patch("telegram_bot_relay.application.custom_emoji.REACTION_METADATA_TIMEOUT", 0.01):
            result = await inspect_custom_emoji(
                InspectCustomEmoji(custom_emoji_ids=["1"], download_thumbnail=True),
                config,
                journal,
                client,
                "test",
            )
        assert result.status == "failed" and result.error == "TimeoutError"
        assert not client.downloaded
        assert list(journal.records())[-1].type == "inspection_completed"
        journal.close()

    asyncio.run(run())
