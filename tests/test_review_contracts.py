"""Regression checks for subscription and operation-specific thread contracts."""

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import JsonValue, ValidationError

from telegram_bot_relay.application.models import operation_adapter
from telegram_bot_relay.telegram.client import Client


@pytest.mark.parametrize(
    "operation",
    [
        {"method": "edit_message", "message_id": 1, "text": "Changed"},
        {"method": "set_reaction", "message_id": 1, "emoji": "\U0001f44d"},
        {"method": "answer_callback", "callback_query_id": "callback"},
    ],
)
def test_unsupported_thread_id_is_rejected_before_transport(
    operation: dict[str, JsonValue],
) -> None:
    with pytest.raises(ValidationError, match="thread_id"):
        operation_adapter.validate_python({**operation, "chat_id": 101, "thread_id": 42})


@pytest.mark.parametrize(
    "operation",
    [
        {"method": "send_message", "text": "Hello"},
        {"method": "send_media", "media": [{"kind": "document", "file_id": "file"}]},
        {"method": "copy_message", "from_chat_id": 202, "message_id": 1},
        {"method": "forward_message", "from_chat_id": 202, "message_id": 1},
    ],
)
def test_supported_thread_id_reaches_telegram(operation: dict[str, JsonValue]) -> None:
    async def run() -> None:
        calls: list[dict[str, str]] = []

        async def api(request: web.Request) -> web.Response:
            calls.append({k: str(v) for k, v in (await request.post()).items()})
            result: JsonValue = {"message_id": 1}
            if operation["method"] != "copy_message":
                result = {"message_id": 1, "date": 1, "chat": {"id": 101, "type": "private"}}
            return web.json_response({"ok": True, "result": result})

        app = web.Application()
        app.router.add_post("/bot{token}/{method}", api)
        async with TestServer(app) as server:
            client = Client("123456:FAKE_LOCAL_TOKEN", str(server.make_url("/")))
            try:
                await client.execute(
                    operation_adapter.validate_python(
                        {**operation, "chat_id": 101, "thread_id": 42}
                    )
                )
                assert calls[0]["message_thread_id"] == "42"
                assert "thread_id" not in calls[0]
            finally:
                await client.close()

    asyncio.run(run())


def test_polling_keeps_reaction_subscriptions_and_unknown_raw_envelopes() -> None:
    async def run() -> None:
        original: dict[str, JsonValue] = {
            "update_id": 1,
            "future_event": {"unknown": [1, None, {"value": "retained"}]},
        }
        requested: list[str] = []

        async def api(request: web.Request) -> web.Response:
            fields = await request.post()
            requested.extend(json.loads(str(fields["allowed_updates"])))
            return web.json_response({"ok": True, "result": [original]})

        app = web.Application()
        app.router.add_post("/bot{token}/getUpdates", api)
        async with TestServer(app) as server:
            client = Client("123456:FAKE_LOCAL_TOKEN", str(server.make_url("/")))
            try:
                assert await client.updates(None, timeout=1) == [original]
                assert {"chat_member", "message_reaction", "message_reaction_count"} <= set(
                    requested
                )
                assert "update_id" not in requested
            finally:
                await client.close()

    asyncio.run(run())
