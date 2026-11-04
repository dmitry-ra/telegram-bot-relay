"""Inspection budgets bound a whole request while preserving available metadata."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import JsonValue

from telegram_bot_relay.application.custom_emoji import InspectCustomEmoji, inspect_custom_emoji
from telegram_bot_relay.journal import writer
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
        deadlines: list[float | None] = []
        real_timeout_at = asyncio.timeout_at

        def observe_deadline(deadline: float | None) -> asyncio.Timeout:
            deadlines.append(deadline)
            return real_timeout_at(deadline)

        class TimedClient(ThumbnailClient):
            async def custom_emoji(self, ids: list[str]) -> list[JsonValue]:
                return [
                    {"custom_emoji_id": value, "thumbnail": {"file_id": "thumb" + value}}
                    for value in ids
                ]

            async def download(self, file_id: str, path: Path, limit: int, timeout: int) -> None:
                self.downloaded.append(file_id)
                if file_id != "thumb1":
                    await asyncio.Event().wait()
                path.write_bytes(b"example file")

        client = TimedClient()
        with patch(
            "telegram_bot_relay.application.custom_emoji.asyncio.timeout_at", observe_deadline
        ):
            result = await inspect_custom_emoji(
                InspectCustomEmoji(custom_emoji_ids=["1", "2", "3"], download_thumbnail=True),
                config,
                journal,
                client,
                "test",
            )
        assert result.status == "ok" and len(result.stickers) == 3
        # The first download has no scheduling point; the second is cancelled by
        # the real production deadline. Under heavy load it may expire earlier.
        assert client.downloaded in ([], ["thumb1"], ["thumb1", "thumb2"])
        assert "thumb3" not in client.downloaded
        assert deadlines and len(set(deadlines)) == 1
        downloads = [item["thumbnail_download"] for item in result.stickers]
        assert downloads[-1] == {"status": "failed", "error": "TimeoutError"}
        assert all(
            isinstance(item, dict)
            and (item["status"] == "saved" or item == {"status": "failed", "error": "TimeoutError"})
            for item in downloads
        )
        assert len(list(journal.root.glob("*/inspections/*/*.webp"))) <= 1
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


@pytest.mark.parametrize("kind", ["public_parent", "symlink_parent", "public_leaf", "symlink_leaf"])
def test_inspection_rejects_unsafe_existing_parent(tmp_path: Path, kind: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        directory = journal.root / journal.period / "inspections"
        outside = tmp_path / "outside"
        outside.mkdir(mode=0o700)
        identity = "a" * 32
        unsafe = directory
        if kind.endswith("leaf"):
            directory.mkdir(mode=0o700)
            unsafe = directory / identity
        if kind.startswith("public"):
            unsafe.mkdir()
            unsafe.chmod(0o755)
        else:
            unsafe.symlink_to(outside, target_is_directory=True)
        client = ThumbnailClient()
        with patch(
            "telegram_bot_relay.application.custom_emoji.uuid4",
            return_value=SimpleNamespace(hex=identity),
        ):
            result = await inspect_custom_emoji(
                InspectCustomEmoji(custom_emoji_ids=["1", "2"], download_thumbnail=True),
                config,
                journal,
                client,
                "test",
            )
        assert result.status == "ok" and len(result.stickers) == 2
        assert not client.downloaded
        assert not list(outside.iterdir())
        assert not list(unsafe.iterdir())
        assert all(
            item["thumbnail_download"] == {"status": "failed", "error": "ValueError"}
            for item in result.stickers
        )
        assert list(journal.records())[-1].type == "inspection_completed"
        journal.close()

    asyncio.run(run())


def test_inspection_parent_entries_are_synced_before_download(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        period = journal.root / journal.period
        synced: list[Path] = []
        real_sync = writer.sync_directory

        def sync(path: Path) -> None:
            real_sync(path)
            synced.append(path)

        class Client(ThumbnailClient):
            async def download(self, file_id: str, path: Path, limit: int, timeout: int) -> None:
                assert period in synced
                assert period / "inspections" in synced
                await super().download(file_id, path, limit, timeout)

        with patch.object(writer, "sync_directory", side_effect=sync):
            result = await inspect_custom_emoji(
                InspectCustomEmoji(custom_emoji_ids=["1"], download_thumbnail=True),
                config,
                journal,
                Client(),
                "test",
            )
        assert result.status == "ok"
        download = result.stickers[0]["thumbnail_download"]
        assert isinstance(download, dict) and download["status"] == "saved"
        assert list(journal.records())[-1].type == "inspection_completed"
        journal.close()

    asyncio.run(run())
