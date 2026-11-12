import asyncio
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from pydantic import ValidationError

from telegram_bot_relay.application.models import DeleteMessage, operation_adapter
from telegram_bot_relay.application.operations import Operations
from telegram_bot_relay.interfaces.server import create_app
from telegram_bot_relay.interfaces.specification import openapi
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.telegram.client import Client
from tests.helpers import configuration
from tests.test_interfaces import running


@pytest.mark.parametrize("surface", ["http", "mcp"])
@pytest.mark.parametrize(
    "scenario", ["success", "group", "denied", "missing", "forbidden", "lost_reply"]
)
def test_deletion_through_real_interfaces_and_local_bot_api(
    tmp_path: Path, surface: str, scenario: str
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config.access.outgoing_chats.append(-202)
        journal = Journal(Path(config.data_dir))
        chat_id = -202 if scenario == "group" else (202 if scenario == "denied" else 101)
        messages = {(chat_id, 42)} if scenario != "missing" else set()
        calls: list[dict[str, str]] = []

        async def api(request: web.Request) -> web.Response:
            assert request.match_info["method"] == "deleteMessage"
            fields = {key: str(value) for key, value in (await request.post()).items()}
            calls.append(fields)
            assert fields == {"chat_id": str(chat_id), "message_id": "42"}
            last = list(journal.records())[-1]
            assert last.type == "operation_requested"
            assert last.data["request"] == {
                "method": "delete_message",
                "chat_id": chat_id,
                "message_id": 42,
            }
            if scenario == "forbidden":
                return web.json_response(
                    {"ok": False, "error_code": 403, "description": "Forbidden"}, status=403
                )
            if (chat_id, 42) not in messages:
                return web.json_response(
                    {"ok": False, "error_code": 400, "description": "Message not found"}, status=400
                )
            messages.remove((chat_id, 42))
            if scenario == "lost_reply":
                assert request.transport is not None
                request.transport.close()
                return web.Response()
            return web.json_response({"ok": True, "result": True})

        fake_api = web.Application()
        fake_api.router.add_post("/bot{token}/{method}", api)
        async with TestServer(fake_api) as bot_api:
            client = Client("123456:FAKE_LOCAL_TOKEN", str(bot_api.make_url("/")).rstrip("/"))
            try:
                async with running(
                    create_app(Operations(config, journal, client), "secret")
                ) as url:
                    async with create_mcp_http_client(
                        headers={"Authorization": "Bearer secret"}
                    ) as http:
                        request = {"method": "delete_message", "chat_id": chat_id, "message_id": 42}
                        outcome: dict[str, Any]
                        if surface == "http":
                            response = await http.post(url + "/v1/operations", json=request)
                            assert response.status_code == 200
                            outcome = response.json()
                            schema = (await http.get(url + "/openapi.json")).json()
                            assert "DeleteMessage" in schema["components"]["schemas"]
                        else:
                            async with streamable_http_client(url + "/mcp", http_client=http) as (
                                read,
                                write,
                            ):
                                async with ClientSession(read, write) as session:
                                    await session.initialize()
                                    tool = next(
                                        t
                                        for t in (await session.list_tools()).tools
                                        if t.name == "delete_message"
                                    )
                                    assert tool.annotations is not None
                                    assert tool.annotations.destructive_hint is True
                                    assert tool.annotations.read_only_hint is False
                                    result = await session.call_tool(
                                        "delete_message", {"request": request}
                                    )
                                    assert not result.is_error
                                    assert result.structured_content is not None
                                    outcome = result.structured_content
                        expected = {
                            "success": "sent",
                            "group": "sent",
                            "denied": "denied",
                            "missing": "failed",
                            "forbidden": "failed",
                            "lost_reply": "uncertain",
                        }[scenario]
                        assert outcome["status"] == expected
                        assert len(calls) == (0 if scenario == "denied" else 1)
                        if expected == "sent":
                            assert outcome["result"] is True
                        if scenario in ("success", "group", "lost_reply"):
                            assert not messages
                        if scenario in ("denied", "forbidden"):
                            assert messages == {(chat_id, 42)}
                        if scenario == "lost_reply":
                            assert outcome["error"] == "telegram_network_error"
                        records = list(journal.records())
                        requested, completed = records[-2:]
                        assert requested.type == "operation_requested"
                        assert completed.type == "operation_completed"
                        assert requested.data["source"] == surface
                        assert (
                            requested.data["operation_id"]
                            == completed.data["operation_id"]
                            == outcome["operation_id"]
                        )
                        assert completed.data["status"] == expected
                        assert not [r for r in records if r.type == "operation_artifacts"]
            finally:
                await client.close()
                journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("message_id", [0, -1, True, "42", None, 1.5])
def test_delete_message_rejects_invalid_ids(message_id: Any) -> None:
    with pytest.raises(ValidationError):
        operation_adapter.validate_python(
            {"method": "delete_message", "chat_id": 101, "message_id": message_id}
        )


def test_delete_message_rejects_unrelated_fields() -> None:
    with pytest.raises(ValidationError):
        DeleteMessage.model_validate({"chat_id": 101, "message_id": 42, "text": "unrelated"})
    schema = openapi()["components"]["schemas"]["DeleteMessage"]
    assert set(schema["required"]) == {"chat_id", "message_id"}
    assert schema["additionalProperties"] is False


def test_invalid_delete_requests_never_reach_operations(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = Client("123456:FAKE_LOCAL_TOKEN", "http://127.0.0.1:1")
        try:
            async with running(create_app(Operations(config, journal, client), "secret")) as url:
                async with create_mcp_http_client(
                    headers={"Authorization": "Bearer secret"}
                ) as http:
                    before = journal.seq
                    response = await http.post(
                        url + "/v1/operations",
                        json={"method": "delete_message", "chat_id": 101, "message_id": 0},
                    )
                    assert response.status_code == 400
                    async with streamable_http_client(url + "/mcp", http_client=http) as (
                        read,
                        write,
                    ):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            result = await session.call_tool(
                                "delete_message", {"request": {"chat_id": 101, "message_id": 0}}
                            )
                            assert result.is_error
                    assert journal.seq == before
        finally:
            await client.close()
            journal.close()

    asyncio.run(run())
