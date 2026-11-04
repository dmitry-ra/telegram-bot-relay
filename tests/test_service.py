import asyncio
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
    from copy import deepcopy

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
