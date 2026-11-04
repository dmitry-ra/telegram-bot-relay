import asyncio
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import JsonValue

from telegram_bot_relay.application.models import (
    Callback,
    EditMessage,
    Media,
    Operation,
    Reaction,
    SendMedia,
    SendMessage,
    Transfer,
)
from telegram_bot_relay.config import Attachments
from telegram_bot_relay.telegram.attachments import descriptors, prepare
from telegram_bot_relay.telegram.client import Client
from tests.helpers import FakeClient, message


def test_sdk_against_mock_api(tmp_path: Path) -> None:
    async def run() -> None:
        calls: list[tuple[str, dict[str, str]]] = []
        original = message()
        original["future_type"] = {"keep": [1, None]}

        async def api(request: web.Request) -> web.Response:
            method = request.match_info["method"]
            fields = {k: str(v) for k, v in (await request.post()).items()}
            calls.append((method, fields))
            event = original["message"]
            assert isinstance(event, dict)
            result: JsonValue = event
            if method == "getUpdates":
                result = [original]
            elif method == "getMe":
                result = {"id": 123456, "is_bot": True, "first_name": "Example"}
            elif method == "getFile":
                result = {
                    "file_id": "one",
                    "file_unique_id": "unique",
                    "file_size": 5,
                    "file_path": "files/one",
                }
            elif method == "sendMediaGroup":
                result = [event, event]
            elif method == "copyMessage":
                result = {"message_id": 1}
            elif method in ("setMessageReaction", "answerCallbackQuery"):
                result = True
            return web.json_response({"ok": True, "result": result})

        async def file(request: web.Request) -> web.Response:
            return web.Response(body=b"hello")

        app = web.Application()
        app.router.add_post("/bot{token}/{method}", api)
        app.router.add_get("/file/bot{token}/files/one", file)
        async with TestServer(app) as server:
            client = Client("123456:FAKE_LOCAL_TOKEN", str(server.make_url("/")).rstrip("/"))
            try:
                assert await client.identity() == 123456
                assert await client.updates(2, timeout=0) == [original]
                assert calls[-1][1]["offset"] == "2"
                operations: list[Operation] = [
                    SendMessage(chat_id=101, text="x", reply_to=1, thread_id=2),
                    EditMessage(chat_id=101, message_id=1, text="edit"),
                    Transfer(method="forward_message", chat_id=101, from_chat_id=101, message_id=1),
                    Transfer(method="copy_message", chat_id=101, from_chat_id=101, message_id=1),
                    Reaction(chat_id=101, message_id=1, emoji="\U0001f440"),
                    Callback(chat_id=101, callback_query_id="callback"),
                    SendMedia(
                        chat_id=101,
                        media=[Media(kind="photo", file_id="a"), Media(kind="video", file_id="b")],
                    ),
                ]
                for operation in operations:
                    assert await client.execute(operation) is not None
                for kind in (
                    "photo",
                    "document",
                    "voice",
                    "audio",
                    "video",
                    "video_note",
                    "animation",
                    "sticker",
                ):
                    media = Media.model_validate({"kind": kind, "file_id": "one"})
                    await client.execute(SendMedia(chat_id=101, media=[media]))
                await client.download("one", tmp_path / "file", 10, 5)
                assert (tmp_path / "file").read_bytes() == b"hello"
                assert (tmp_path / "file").stat().st_mode & 0o077 == 0
                protected = tmp_path / "protected"
                protected.write_bytes(b"retained")
                link = tmp_path / "link"
                link.symlink_to(protected)
                with pytest.raises(FileExistsError):
                    await client.download("one", link, 10, 5)
                assert protected.read_bytes() == b"retained"
                with pytest.raises(FileExistsError):
                    await client.download("one", protected, 10, 5)
                assert protected.read_bytes() == b"retained"
                with pytest.raises(ValueError):
                    await client.download("one", tmp_path / "oversize", 4, 5)
            finally:
                await client.close()

    asyncio.run(run())


def test_attachment_limits_and_failures(tmp_path: Path) -> None:
    async def run() -> None:
        client = FakeClient()
        original: dict[str, JsonValue] = {
            "photo": [{"file_id": "one"}, {"file_id": "one"}],
            "document": {"file_id": "two", "file_size": 99},
        }
        assert len(descriptors(original)) == 2
        settings = Attachments(max_bytes=20)
        result = await prepare(client, original, tmp_path / "attachments", settings, True)
        assert [r["status"] for r in result] == ["saved", "size_limited"]
        assert client.downloaded == ["one"]
        settings.download = False
        assert (await prepare(client, original, tmp_path / "attachments", settings, True))[0][
            "status"
        ] == "skipped_policy"
        settings.download = True
        client.failure = OSError("download failed")
        result = await prepare(client, original, tmp_path / "attachments", settings, True)
        assert result[0]["status"] == "failed"
        assert not list((tmp_path / "attachments").glob("*.part"))

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["public", "symlink"])
def test_attachments_reject_unsafe_existing_directory(tmp_path: Path, kind: str) -> None:
    async def run() -> None:
        directory = tmp_path / "attachments"
        if kind == "public":
            directory.mkdir()
            directory.chmod(0o755)
        else:
            outside = tmp_path / "outside"
            outside.mkdir(mode=0o700)
            directory.symlink_to(outside, target_is_directory=True)
        client = FakeClient()
        with pytest.raises(ValueError, match="permissions|Symlink directory"):
            await prepare(client, {"document": {"file_id": "one"}}, directory, Attachments(), True)
        assert not client.downloaded

    asyncio.run(run())
