"""Incoming type hints, safe naming and unchanged durable Telegram envelopes."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import JsonValue

from telegram_bot_relay.config import Attachments
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import Service
from telegram_bot_relay.telegram.attachments import descriptors, located_descriptors, prepare
from telegram_bot_relay.telegram.file_types import claim, hints, suffix, telegram_path
from tests.helpers import FakeClient, configuration, message


class TypedClient(FakeClient):
    def __init__(self, metadata: dict[str, JsonValue] | None = None) -> None:
        super().__init__()
        self.metadata = metadata

    async def download(
        self, file_id: str, path: Path, limit: int, timeout: int
    ) -> dict[str, JsonValue] | None:
        await super().download(file_id, path, limit, timeout)
        return self.metadata


@pytest.mark.parametrize(
    ("kind", "descriptor", "api_path", "extension", "source"),
    [
        ("voice", {"mime_type": "audio/ogg", "duration": 3}, "voice/file.oga", ".ogg", "mime_type"),
        ("photo", {"width": 100, "height": 100}, "photos/file.JPG", ".jpg", "telegram_file_path"),
        (
            "document",
            {"file_name": "report.PDF", "mime_type": "application/pdf"},
            "documents/file.pdf",
            ".pdf",
            "file_name",
        ),
        ("audio", {"mime_type": "audio/mpeg"}, "music/file.mp3", ".mp3", "mime_type"),
        ("video", {"mime_type": "video/quicktime"}, "video/file.mov", ".mov", "mime_type"),
        ("video_note", {"duration": 4}, "video/file.mp4", ".mp4", "telegram_file_path"),
        ("animation", {"mime_type": "video/mp4"}, "animations/file.mp4", ".mp4", "mime_type"),
        (
            "sticker",
            {"is_animated": False, "is_video": False},
            "stickers/file.webp",
            ".webp",
            "telegram_file_path",
        ),
        (
            "sticker",
            {"is_animated": True, "is_video": False},
            "stickers/file",
            ".tgs",
            "media_kind",
        ),
        (
            "sticker",
            {"is_animated": False, "is_video": True},
            "stickers/file",
            ".webm",
            "media_kind",
        ),
    ],
)
def test_media_types_use_declared_or_getfile_hints_without_touching_original(
    tmp_path: Path,
    kind: str,
    descriptor: dict[str, JsonValue],
    api_path: str,
    extension: str,
    source: str,
) -> None:
    async def run() -> None:
        media: JsonValue = {"file_id": "one", **descriptor}
        if kind == "photo":
            media = [media]
        original: dict[str, JsonValue] = {"message": {kind: media}}
        before = deepcopy(original)
        client = TypedClient({"file_path": api_path, "file_size": 999})
        saved = (await prepare(client, original, tmp_path / "attachments", Attachments(), True))[0]
        assert saved["status"] == "saved"
        path = tmp_path / str(saved["path"])
        assert path.suffix == extension
        assert path.read_bytes() == b"example file"
        assert saved["bytes"] == len(b"example file")
        typed = saved["type_hints"]
        assert isinstance(typed, dict)
        assert typed["media_kinds"] == [kind]
        assert typed["naming_source"] == source
        assert typed["telegram_file_size"] == 999
        assert typed["telegram_file_path"] == api_path
        assert "detected_mime_type" not in typed
        assert original == before
        assert not list((tmp_path / "attachments").glob("*.part"))

    asyncio.run(run())


def test_conflicting_name_mime_and_remote_path_are_explicit_claims() -> None:
    typed = hints(
        {"file_name": "capture.JPG", "mime_type": "image/heic"},
        ("document",),
        {"file_path": "documents/capture.heic"},
    )
    assert typed == {
        "media_kinds": ["document"],
        "file_name": "capture.JPG",
        "mime_type": "image/heic",
        "telegram_file_path": "documents/capture.heic",
        "suffix": ".jpg",
        "naming_source": "file_name",
        "conflicts": ["file_name_mime_type", "file_name_telegram_file_path"],
    }
    assert (
        hints(
            {"file_name": "picture.jpeg", "mime_type": "image/jpeg"},
            ("photo",),
            {"file_path": "photos/file.jpg"},
        ).get("conflicts")
        is None
    )
    assert hints({"mime_type": "audio/ogg; codecs=opus"}, ("voice",))["suffix"] == ".ogg"
    assert hints({"mime_type": "audio/mpeg"}, ("audio",), {"file_path": "voice/file.ogg"})[
        "conflicts"
    ] == ["mime_type_telegram_file_path"]


@pytest.mark.parametrize(
    "name", ["../../report.pdf", "C:\\private\\report.PDF", "/private/report.pdf"]
)
def test_original_path_never_becomes_a_local_basename(tmp_path: Path, name: str) -> None:
    async def run() -> None:
        result = await prepare(
            TypedClient(),
            {"document": {"file_id": "x", "file_name": name}},
            tmp_path / "attachments",
            Attachments(),
            True,
        )
        path = tmp_path / str(result[0]["path"])
        assert path.parent == tmp_path / "attachments"
        assert len(path.stem) == 32 and all(char in "0123456789abcdef" for char in path.stem)
        assert path.suffix == ".pdf"
        assert not (tmp_path / "report.pdf").exists()

    asyncio.run(run())


def test_unrecognized_future_files_and_invalid_hints_are_kept_as_binary(tmp_path: Path) -> None:
    async def run() -> None:
        original: dict[str, JsonValue] = {
            "future_media": {
                "file_id": "future",
                "file_name": "new.unknown",
                "mime_type": "application/x-future",
            }
        }
        saved = (
            await prepare(
                TypedClient({"file_path": "files/new.unknown"}),
                original,
                tmp_path / "attachments",
                Attachments(),
                True,
            )
        )[0]
        assert saved["status"] == "saved" and str(saved["path"]).endswith(".bin")
        assert isinstance(saved["type_hints"], dict) and saved["type_hints"]["media_kinds"] == [
            "unknown"
        ]
        assert descriptors(original) == [original["future_media"]]
        assert (
            hints({"file_name": None, "mime_type": False}, ("unknown",))["naming_source"]
            == "unknown"
        )
        assert hints({"is_animated": True, "is_video": True}, ("sticker",))["suffix"] == ".bin"
        assert hints({"is_animated": False, "is_video": False}, ("sticker",))["suffix"] == ".bin"
        assert hints({"mime_type": "application/octet-stream"}, ("voice",))["suffix"] == ".bin"

    asyncio.run(run())


@pytest.mark.parametrize("value", [None, 1, "", "x" * 1025, "nul\x00.pdf"])
def test_unusable_metadata_is_bounded(value: JsonValue) -> None:
    assert claim(value) is None


@pytest.mark.parametrize(
    "value", ["evil.pdf\n", "evil.verylongextension", "file.bin", "file.exe", None]
)
def test_suffix_is_an_explicit_allowlist(value: str | None) -> None:
    assert suffix(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "https://api.invalid/botSECRET/file.pdf",
        "files/f.pdf?token=SECRET",
        "files/f.pdf#SECRET",
        "files/f.pdf\n",
    ],
)
def test_api_urls_or_controls_are_never_archived_as_file_paths(value: str) -> None:
    assert telegram_path(value) is None
    assert "telegram_file_path" not in hints({}, ("unknown",), {"file_path": value})


def test_local_bot_api_absolute_path_is_only_a_naming_hint() -> None:
    assert (
        hints({}, ("document",), {"file_path": "/private/bot/files/report.pdf"})["suffix"] == ".pdf"
    )


def test_duplicate_ids_keep_all_media_claims_and_legacy_descriptor_result() -> None:
    original: dict[str, JsonValue] = {
        "animation": {"file_id": "one", "mime_type": "video/mp4"},
        "document": {"file_id": "one", "file_name": "file.mp4"},
    }
    assert descriptors(original) == [original["document"]]
    assert located_descriptors(original) == [(original["document"], ("animation", "document"))]
    assert original["animation"] != original["document"]


def test_size_policy_download_failure_and_no_return_consumers_preserve_contract(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        original: dict[str, JsonValue] = {
            "voice": {"file_id": "one", "mime_type": "audio/ogg", "file_size": 99}
        }
        client = TypedClient()
        limited = await prepare(
            client, original, tmp_path / "attachments", Attachments(max_bytes=20), True
        )
        assert limited[0]["status"] == "size_limited" and not client.downloaded
        skipped = await prepare(client, original, tmp_path / "attachments", Attachments(), False)
        assert skipped[0]["status"] == "skipped_policy" and not client.downloaded
        client.failure = OSError("private transport error")
        failed = await prepare(client, original, tmp_path / "attachments", Attachments(), True)
        assert failed[0]["status"] == "failed" and failed[0]["error"] == "OSError"
        assert "private transport error" not in str(failed)
        assert not list((tmp_path / "attachments").glob("*.part"))
        # Existing third-party/FakeClient overrides returning None still work.
        saved = await prepare(
            FakeClient(),
            {"voice": {"file_id": "two", "mime_type": "audio/ogg"}},
            tmp_path / "attachments",
            Attachments(),
            True,
        )
        assert saved[0]["status"] == "saved" and str(saved[0]["path"]).endswith(".ogg")

    asyncio.run(run())


def test_reply_forward_album_and_raw_updates_survive_prepare_and_restart(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        service = Service(config, journal, TypedClient({"file_path": "photos/file.jpg"}))
        original = message()
        event = original["message"]
        assert isinstance(event, dict)
        event.update(
            media_group_id="album-1",
            caption="Caption",
            message_thread_id=8,
            forward_origin={"type": "hidden_user", "sender_user_name": "Synthetic", "date": 123},
            reply_to_message={
                "message_id": 20,
                "voice": {"file_id": "parent", "mime_type": "audio/ogg"},
            },
            quote={"text": "Selected quote"},
            photo=[{"file_id": "small"}, {"file_id": "large"}],
            future_field={"keep": [None, "original"]},
        )
        before = deepcopy(original)
        source = service.ingest(original)
        assert await service.process_once()
        received = next(record for record in journal.records() if record.type == "message_received")
        prepared = next(record for record in journal.records() if record.type == "message_prepared")
        assert received.seq == source and received.data["original"] == before
        assert original == before
        assets = prepared.data["attachments"]
        assert isinstance(assets, list) and len(assets) == 3
        assert all(
            isinstance(asset, dict) and isinstance(asset["type_hints"], dict) for asset in assets
        )
        journal.close()
        reopened = Journal(Path(config.data_dir))
        recovered = Service(config, reopened, TypedClient())
        assert await recovered.process_once() is False
        assert (
            next(record for record in reopened.records() if record.type == "message_received").data[
                "original"
            ]
            == before
        )
        reopened.close()

    asyncio.run(run())
