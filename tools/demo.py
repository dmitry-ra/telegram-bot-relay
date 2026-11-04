"""Show the real archive/MCP pipeline with synthetic Telegram transport only."""

import argparse
import asyncio
import json
import logging
import socket
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from aiohttp import web
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from starlette.types import ASGIApp

from telegram_bot_relay.config import Access, Attachments, Config
from telegram_bot_relay.interfaces.server import create_app
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import Service
from telegram_bot_relay.telegram.client import Client

API_KEY = "synthetic-demo-api"


@asynccontextmanager
async def running(app: ASGIApp) -> AsyncIterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on")
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        task.result()
                        raise RuntimeError("Demo server stopped")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)


async def demo(pace: float) -> None:
    fixture = json.loads(Path(__file__).with_name("demo").joinpath("fixture.json").read_text())
    outbox: list[dict[str, str]] = []

    async def send_message(request: web.Request) -> web.Response:
        form = await request.post()
        item = {key: str(value) for key, value in form.items()}
        outbox.append(item)
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "message_id": 100 + len(outbox),
                    "date": 1700000001,
                    "chat": {"id": int(item["chat_id"]), "type": "private"},
                    "text": item["text"],
                },
            }
        )

    bot = web.Application()
    bot.router.add_post("/bot123456:demo-only/sendMessage", send_message)
    runner = web.AppRunner(bot, access_log=None)
    await runner.setup()
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        site = web.SockSite(runner, listener)
        await site.start()
        endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"
        with tempfile.TemporaryDirectory(prefix="relay-demo-") as folder:
            root = Path(folder)
            config = Config(
                token_file="unused",
                api_token_file="unused",
                data_dir=str(root / "archive"),
                access=Access(allowed_users=[42], outgoing_chats=[42]),
                attachments=Attachments(download=False),
            )
            journal = Journal(Path(config.data_dir))
            client = Client("123456:demo-only", api_url=endpoint)
            service = Service(config, journal, client)
            try:
                print("\033[2J\033[H\033[1;36mTELEGRAM BOT RELAY | standalone demo\033[0m")
                print("Synthetic Telegram transport / actual Relay and MCP\n", flush=True)
                await asyncio.sleep(2 * pace)
                print("\033[1;33mTELEGRAM (synthetic input)\033[0m")
                print("Casey: " + fixture["message"]["text"], flush=True)
                service.ingest(fixture)
                assert await service.process_once()
                await asyncio.sleep(3 * pace)
                received = next(r for r in journal.records() if r.type == "message_received")
                decision = received.data["access"]
                assert isinstance(decision, dict)
                print("\n\033[1;32mRELAY (actual local archive)\033[0m")
                print(f"update {fixture['update_id']} committed | allowed={decision['allowed']}")
                print("Original content retained; Gateway is optional.", flush=True)
                await asyncio.sleep(3 * pace)
                async with running(create_app(service.operations, API_KEY)) as url:
                    async with create_mcp_http_client(
                        headers={"Authorization": "Bearer " + API_KEY}
                    ) as http:
                        async with streamable_http_client(url + "/mcp", http_client=http) as (
                            read,
                            write,
                        ):
                            async with ClientSession(read, write) as session:
                                await session.initialize()
                                args = {
                                    "chat_id": 42,
                                    "reply_to": 41,
                                    "text": "Build. Test. Deploy.",
                                }
                                print("\n\033[1;35mCLIENT (actual MCP request)\033[0m")
                                print("reply(chat_id=42, reply_to=41)")
                                print('text="' + str(args["text"]) + '"', flush=True)
                                result = await session.call_tool("reply", {"request": args})
                                assert not result.is_error
                                outcome: dict[str, Any] = result.structured_content or {}
                                assert outcome["status"] == "sent"
                                await asyncio.sleep(3 * pace)
                                assert len(outbox) == 1
                                delivered = outbox[0]
                                reply = json.loads(delivered["reply_parameters"])
                                assert reply["message_id"] == 41
                                print("\n\033[1;33mTELEGRAM (synthetic Bot API outbox)\033[0m")
                                print("Relay: " + delivered["text"])
                                result_data = outcome["result"]
                                print(
                                    f"{outcome['status']} | message_id={result_data['message_id']}"
                                    f" | reply_to={reply['message_id']}",
                                    flush=True,
                                )
                                await asyncio.sleep(3 * pace)
                types = [record.type for record in journal.records()]
                assert "operation_requested" in types and "operation_completed" in types
                print("\n\033[1;32mArchive: inbound + requested + completed records\033[0m")
                print("No real bot, model or private configuration used.", flush=True)
                await asyncio.sleep(4 * pace)
            finally:
                service.close()
                await client.close()
                journal.close()
    finally:
        await runner.cleanup()
        listener.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pace", type=float, default=1, help="Delay multiplier; zero for smoke")
    args = parser.parse_args()
    if args.pace < 0:
        parser.error("pace must be nonnegative")
    logging.basicConfig(level=logging.CRITICAL)
    asyncio.run(demo(args.pace))


if __name__ == "__main__":
    main()
