import asyncio
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import JsonValue

from telegram_bot_relay.config import Gateway
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import GatewayResponseError, Service
from tests.helpers import FakeClient, configuration, message


@pytest.mark.parametrize(
    ("fields", "preview"),
    [
        ({"text": "Full text"}, "Full text"),
        (
            {
                "text": "Reply",
                "reply_to_message": {"message_id": 42, "text": "Parent"},
                "quote": {"text": "Chosen"},
            },
            '\u21a9 #42 "Chosen" | Reply',
        ),
        ({"photo": [{"file_id": "image"}], "caption": "Sky"}, "\U0001f5bc\ufe0f Photo | Sky"),
        (
            {"document": {"file_id": "doc", "file_name": "report.pdf"}},
            "\U0001f4ce Document | report.pdf",
        ),
        ({"voice": {"file_id": "voice", "duration": 4}}, "\U0001f3a4 Voice | 4s"),
    ],
)
def test_forwarded_preview_and_original_content(
    tmp_path: Path, fields: dict[str, JsonValue], preview: str
) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    async def run() -> None:
        bodies: list[dict[str, JsonValue]] = []

        async def receive(request: web.Request) -> web.Response:
            bodies.append(await request.json())
            return web.json_response({"id": "accepted"}, status=202)

        app = web.Application()
        app.router.add_post("/v1/targets/work/messages", receive)
        async with TestServer(app) as server:
            config = configuration(tmp_path)
            config.gateway = Gateway(
                url=str(server.make_url("/")).rstrip("/"), target="work", start="oldest"
            )
            journal = Journal(Path(config.data_dir))
            service = Service(config, journal, FakeClient())
            original = message()
            event = original["message"]
            assert isinstance(event, dict)
            event.pop("text")
            event.update(fields)
            before = deepcopy(original)
            service.ingest(original)
            assert await service.process_once()
            assert await service.forward_once()
            assert len(bodies) == 1
            assert bodies[0]["preview"] == preview
            content = bodies[0]["content"]
            assert isinstance(content, dict)
            assert {k: v for k, v in content.items() if k != "downloads"} == before
            assert original == before
            if "text" not in fields:
                downloads = content["downloads"]
                assert isinstance(downloads, list)
                assert len(downloads) == 1
                saved = downloads[0]
                assert isinstance(saved, dict)
                assert saved["status"] == "saved"
                assert Path(str(saved["path"])).read_bytes() == b"example file"
            journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("clear", [False, True])
@pytest.mark.parametrize("username", [None, "example_user"])
def test_reaction_forwarding_preserves_original_and_actor(
    tmp_path: Path, clear: bool, username: str | None
) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    async def run() -> None:
        bodies: list[dict[str, JsonValue]] = []

        async def receive(request: web.Request) -> web.Response:
            bodies.append(await request.json())
            return web.json_response({"id": "accepted"}, status=202)

        app = web.Application()
        app.router.add_post("/v1/targets/work/messages", receive)
        async with TestServer(app) as server:
            config = configuration(tmp_path)
            config.gateway = Gateway(
                url=str(server.make_url("/")).rstrip("/"), target="work", start="oldest"
            )
            journal = Journal(Path(config.data_dir))
            service = Service(config, journal, FakeClient())
            actor: dict[str, JsonValue] = {"id": 101, "first_name": "Example"}
            if username:
                actor["username"] = username
            reactions: list[JsonValue] = [{"type": "emoji", "emoji": "\U0001f44d"}]
            original: dict[str, JsonValue] = {
                "update_id": 1,
                "message_reaction": {
                    "message_id": 42,
                    "date": 1793487600,
                    "chat": {"id": 101, "type": "private"},
                    "user": actor,
                    "old_reaction": reactions if clear else [],
                    "new_reaction": [] if clear else reactions,
                },
            }
            before = deepcopy(original)
            service.ingest(original)
            assert await service.process_once()
            assert await service.forward_once()
            assert len(bodies) == 1
            sent = bodies[0]
            action = "Clear" if clear else "Set"
            assert sent["preview"] == f"Reaction #42 | {action} \U0001f44d"
            assert sent["sender"] == (username or "Example")
            content = sent["content"]
            assert isinstance(content, dict)
            assert content == {**before, "downloads": []}
            assert original == before
            journal.close()

    asyncio.run(run())


def test_all_input_archived_before_offset_and_recovered(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    journal = Journal(Path(config.data_dir))
    service = Service(config, journal, FakeClient())
    original = message(user_id=202)
    service.ingest(original)
    assert service.offset == 2
    assert service.ingest(original) is None
    record = list(journal.records())[-1]
    assert record.data["original"] == original
    assert record.data["access"] == {"allowed": False, "reason": "user_not_allowed"}
    with patch.object(journal, "append", side_effect=OSError("full")):
        with pytest.raises(OSError):
            service.ingest(message(update_id=2))
    assert service.offset == 2
    journal.close()
    journal = Journal(Path(config.data_dir))
    service = Service(config, journal, FakeClient())
    assert service.offset == 2
    assert service.ingest(original) is None
    journal.close()


@pytest.mark.parametrize("resolved", [True, False])
def test_custom_metadata_is_preserved_for_forward_retry_and_restart(
    tmp_path: Path, resolved: bool
) -> None:
    from aiohttp import ClientResponseError, web
    from aiohttp.test_utils import TestServer

    class ReactionClient(FakeClient):
        calls = 0

        async def custom_emoji(self, identities: list[str]) -> list[JsonValue]:
            self.calls += 1
            assert identities == ["123"]
            return (
                [
                    {
                        "custom_emoji_id": "123",
                        "emoji": "\U0001f44b",
                        "set_name": "ExamplePack",
                        "width": 100,
                        "is_video": True,
                    }
                ]
                if resolved
                else []
            )

    async def run() -> None:
        bodies: list[dict[str, JsonValue]] = []

        async def receive(request: web.Request) -> web.Response:
            bodies.append(await request.json())
            return web.json_response({"id": "accepted"}, status=500 if len(bodies) == 1 else 202)

        app = web.Application()
        app.router.add_post("/v1/targets/work/messages", receive)
        async with TestServer(app) as server:
            config = configuration(tmp_path)
            config.gateway = Gateway(
                url=str(server.make_url("/")).rstrip("/"), target="work", start="oldest"
            )
            journal = Journal(Path(config.data_dir))
            client = ReactionClient()
            service = Service(config, journal, client)
            original: dict[str, JsonValue] = {
                "update_id": 1,
                "message_reaction": {
                    "message_id": 42,
                    "date": 1793487600,
                    "chat": {"id": 101, "type": "private"},
                    "user": {"id": 101, "username": "example_user"},
                    "old_reaction": [],
                    "new_reaction": [{"type": "custom_emoji", "custom_emoji_id": "123"}],
                },
            }
            before = deepcopy(original)
            service.ingest(original)
            assert await service.process_once()
            assert client.calls == 1
            with pytest.raises(ClientResponseError):
                await service.forward_once()
            journal.close()
            journal = Journal(Path(config.data_dir))
            restored = Service(config, journal, client)
            assert not await restored.process_once()
            assert await restored.forward_once()
            assert client.calls == 1
            assert bodies[0] == bodies[1]
            expected = "\U0001f44b (custom, ExamplePack)" if resolved else "Custom emoji 123"
            assert bodies[1]["preview"] == "Reaction #42 | Set " + expected
            content = bodies[1]["content"]
            assert isinstance(content, dict)
            metadata = content["reaction_metadata"]
            assert isinstance(metadata, dict)
            assert metadata["status"] == "ok"
            assert metadata["missing_ids"] == ([] if resolved else ["123"])
            assert {
                k: v for k, v in content.items() if k not in ("downloads", "reaction_metadata")
            } == before
            if resolved:
                assert metadata["stickers"] == [
                    {
                        "custom_emoji_id": "123",
                        "emoji": "\U0001f44b",
                        "set_name": "ExamplePack",
                        "width": 100,
                        "is_video": True,
                    }
                ]
            assert original == before
            journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("download_denied", [False, True])
def test_denied_attachment_option_and_notification(tmp_path: Path, download_denied: bool) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config.attachments.download_denied = download_denied
        config.notify_chats = [101]
        journal = Journal(Path(config.data_dir))
        client = FakeClient()
        service = Service(config, journal, client)
        original = message(user_id=202)
        event = original["message"]
        assert isinstance(event, dict)
        event["document"] = {"file_id": "one", "file_name": "../../report.txt"}
        seq = service.ingest(original)
        journal.rotate()
        assert await service.process_once()
        assert client.downloaded == (["one"] if download_denied else [])
        assert len(client.sent) == 1
        service.ingest(message(update_id=2, user_id=202))
        assert await service.process_once()
        assert len(client.sent) == 2
        assert not await service.process_once()
        assert not await service.forward_once()
        assert seq is not None
        source = journal.find(seq)
        assert len(list((journal.root / source.period / "attachments").glob("*"))) == (
            1 if download_denied else 0
        )
        journal.close()

    asyncio.run(run())


def test_invalid_gateway_response_remains_pending(tmp_path: Path) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    async def run() -> None:
        async def invalid(request: web.Request) -> web.Response:
            return web.json_response(["invalid acceptance"])

        app = web.Application()
        app.router.add_post("/v1/targets/duty/messages", invalid)
        async with TestServer(app) as server:
            config = configuration(tmp_path)
            config.gateway = Gateway(
                url=str(server.make_url("/")).rstrip("/"), target="duty", start="oldest"
            )
            journal = Journal(Path(config.data_dir))
            service = Service(config, journal, FakeClient())
            service.ingest(message())
            await service.process_once()
            with pytest.raises(GatewayResponseError):
                await service.forward_once()
            assert not any(r.type == "gateway_accepted" for r in journal.records())
            service.ingest(message(update_id=2))
            assert service.offset == 3
            journal.close()

    asyncio.run(run())


def test_gateway_outage_and_recovery(tmp_path: Path) -> None:
    import aiohttp
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    async def run() -> None:
        bodies: list[object] = []
        fail = True

        async def receive(request: web.Request) -> web.Response:
            if fail:
                return web.json_response({"error": "unavailable"}, status=503)
            bodies.append(await request.json())
            return web.json_response({"id": "gateway-one"}, status=202)

        app = web.Application()
        app.router.add_post("/v1/targets/duty/messages", receive)
        async with TestServer(app) as server:
            config = configuration(tmp_path)
            config.gateway = Gateway(
                url=str(server.make_url("/")).rstrip("/"), target="duty", start="oldest"
            )
            journal = Journal(Path(config.data_dir))
            service = Service(config, journal, FakeClient())
            service.ingest(message())
            service.ingest(message(update_id=2, user_id=202))
            await service.process_once()
            await service.process_once()
            journal.rotate()
            with pytest.raises(aiohttp.ClientResponseError):
                await service.forward_once()
            service.ingest(message(update_id=3))
            assert service.offset == 4
            fail = False
            assert await service.forward_once()
            assert not await service.forward_once()
            assert len(bodies) == 1
            journal.close()
            journal = Journal(Path(config.data_dir))
            service = Service(config, journal, FakeClient())
            assert not await service.forward_once()
            assert await service.process_once()
            assert await service.forward_once()
            assert len(bodies) == 2
            journal.close()

    asyncio.run(run())


def test_every_denial_notifies_every_recipient_without_time_filter(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config.access.outgoing_chats = [101, 102]
        config.notify_chats = [101, 102]
        config.notify_interval = 999999
        journal = Journal(Path(config.data_dir))
        client = FakeClient()
        service = Service(config, journal, client)
        for update in range(1, 5):
            original = message(update_id=update, user_id=202 + update)
            service.ingest(original)
            assert service.ingest(original) is None
        for _ in range(4):
            assert await service.process_once()
        assert len(client.sent) == 8
        assert [operation.chat_id for operation in client.sent] == [101, 102] * 4
        assert all(operation.method == "send_message" for operation in client.sent)
        assert not await service.process_once()
        journal.close()

    asyncio.run(run())
