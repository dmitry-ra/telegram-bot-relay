import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from starlette.types import ASGIApp

from telegram_bot_relay.application.operations import Operations
from telegram_bot_relay.interfaces.server import create_app
from telegram_bot_relay.interfaces.specification import openapi
from telegram_bot_relay.journal.writer import Journal
from tests.helpers import FakeClient, configuration


@asynccontextmanager
async def running(app: ASGIApp) -> AsyncIterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on")
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("Server did not start")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


def test_real_http_and_mcp_share_operations_and_audit(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = FakeClient()
        async with running(create_app(Operations(config, journal, client), "secret")) as url:
            async with create_mcp_http_client(headers={"Authorization": "Bearer secret"}) as http:
                response = await http.post(
                    url + "/v1/operations",
                    json={"method": "send_message", "chat_id": 101, "text": "HTTP"},
                )
                assert response.status_code == 200
                assert response.json()["status"] == "sent"
                document = openapi()
                schema = {
                    "$ref": "#/components/schemas/Outcome",
                    "components": document["components"],
                }
                Draft202012Validator(schema).validate(response.json())
                assert (await http.get(url + "/openapi.json")).json() == document
                assert (await http.get(url + "/healthz")).json()["status"] == "ready"
                async with streamable_http_client(url + "/mcp", http_client=http) as (
                    read,
                    write,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert "reply" in {t.name for t in tools.tools}
                        result = await session.call_tool(
                            "reply", {"request": {"chat_id": 101, "text": "MCP", "reply_to": 1}}
                        )
                        assert not result.is_error
                        invalid = await session.call_tool(
                            "reply", {"request": {"chat_id": 101, "text": "missing reply"}}
                        )
                        assert invalid.is_error
                        denied = await session.call_tool(
                            "send_message", {"request": {"chat_id": 202, "text": "denied"}}
                        )
                        assert not denied.is_error
            assert len(client.sent) == 2
            requests = [r for r in journal.records() if r.type == "operation_requested"]
            assert [r.data["source"] for r in requests] == ["http", "mcp", "mcp"]
        journal.close()

    asyncio.run(run())


def test_http_security_and_validation(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        async with running(create_app(Operations(config, journal, FakeClient()), "secret")) as url:
            async with create_mcp_http_client() as http:
                assert (await http.get(url + "/healthz")).status_code == 401
                headers = {"Authorization": "Bearer secret"}
                assert (
                    await http.post(url + "/v1/operations", content="{", headers=headers)
                ).status_code == 400
                assert (
                    await http.post(
                        url + "/v1/operations",
                        json={"method": "send_message", "chat_id": "101", "text": "x"},
                        headers=headers,
                    )
                ).status_code == 400
                assert (
                    await http.get(
                        url + "/healthz", headers={**headers, "Origin": "https://evil.example"}
                    )
                ).status_code == 403
                assert (
                    await http.get(url + "/healthz", headers={**headers, "Host": "evil.example"})
                ).status_code == 403
                for host in (
                    "[",
                    "[invalid]",
                    "localhost:bad:port",
                    "user@localhost",
                    "localhost/path",
                ):
                    assert (
                        await http.get(url + "/healthz", headers={**headers, "Host": host})
                    ).status_code == 403
                assert (
                    await http.post(
                        url + "/v1/operations", content=b"x" * 1_048_577, headers=headers
                    )
                ).status_code == 413
        journal.close()

    asyncio.run(run())
