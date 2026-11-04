"""Opt-in real bot trial; all network activity is limited by the supplied config."""

import argparse
import asyncio
import json
import struct
import zlib
from pathlib import Path

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from telegram_bot_relay.config import Config, load, secret
from telegram_bot_relay.interfaces.server import create_app
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import Service
from telegram_bot_relay.telegram.client import Client


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    rows = b"".join(b"\x00" + b"\xff\x00\x00" * 40 + b"\x00\x00\xff" * 40 for _ in range(60))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 80, 60, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


async def trial(config: Config, chat: int, directory: Path, seconds: int) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    document = directory / "relay-document.txt"
    document.write_text("RELAY_DOCUMENT_ORBIT_42\n")
    image = directory / "relay-image.png"
    image.write_bytes(png())
    config.attachments.upload_roots.append(str(directory.resolve()))
    journal = Journal(Path(config.data_dir), config.rotate, config.timezone)
    client = Client(secret(config.token_file))
    service = Service(config, journal, client)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(service.operations, secret(config.api_token_file)),
            host="127.0.0.1",
            port=config.port,
            log_level="warning",
            access_log=False,
        )
    )
    tasks = [asyncio.create_task(service.run()), asyncio.create_task(server.serve())]
    outcomes: list[object] = []
    try:
        async with asyncio.timeout(15):
            while not server.started or service.bot_id is None:
                for task in tasks:
                    if task.done():
                        task.result()
                await asyncio.sleep(0.05)
        url = f"http://127.0.0.1:{config.port}"
        async with create_mcp_http_client(
            headers={"Authorization": "Bearer " + secret(config.api_token_file)}
        ) as http:
            response = await http.post(
                url + "/v1/operations",
                json={
                    "method": "send_message",
                    "chat_id": chat,
                    "text": "Telegram Bot Relay: HTTP test passed. Send RELAY_INBOUND_CHECK to this bot to test incoming delivery.",
                },
            )
            outcomes.append(response.json())
            async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for tool, request in [
                        (
                            "send_message",
                            {
                                "chat_id": chat,
                                "text": "Telegram Bot Relay: Streamable HTTP MCP test.",
                            },
                        ),
                        (
                            "send_media",
                            {
                                "chat_id": chat,
                                "media": [
                                    {
                                        "kind": "document",
                                        "path": str(document.resolve()),
                                        "caption": "Relay document test",
                                    }
                                ],
                            },
                        ),
                        (
                            "send_media",
                            {
                                "chat_id": chat,
                                "media": [
                                    {
                                        "kind": "photo",
                                        "path": str(image.resolve()),
                                        "caption": "Relay image test: red and blue",
                                    }
                                ],
                            },
                        ),
                    ]:
                        result = await session.call_tool(tool, {"request": request})
                        outcomes.append(result.model_dump(mode="json"))
                        if result.is_error:
                            raise RuntimeError("MCP trial operation failed")
            print("REAL_BOT_READY", flush=True)
            async with asyncio.timeout(seconds):
                while True:
                    for task in tasks:
                        if task.done():
                            task.result()
                            raise RuntimeError("Trial server stopped")
                    messages = [r for r in journal.records() if r.type == "message_received"]
                    if any("RELAY_INBOUND_CHECK" in json.dumps(r.data) for r in messages):
                        await asyncio.sleep(2)
                        break
                    await asyncio.sleep(0.2)
    except TimeoutError:
        print("No control incoming message during trial window", flush=True)
    finally:
        server.should_exit = True
        tasks[0].cancel()
        try:
            await asyncio.wait_for(asyncio.shield(tasks[1]), 5)
        except TimeoutError:
            tasks[1].cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.close()
        (directory / "outcomes.json").write_text(
            json.dumps(outcomes, indent=2, ensure_ascii=True) + "\n"
        )
        records = list(journal.records())
        report = {
            "records": len(records),
            "inbound": sum(r.type == "message_received" for r in records),
            "operations": [r.data for r in records if r.type == "operation_completed"],
            "periods": len(list(journal.root.glob("*/events.jsonl"))),
        }
        (directory / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=True) + "\n"
        )
        print(
            json.dumps(
                {
                    "records": report["records"],
                    "inbound": report["inbound"],
                    "periods": report["periods"],
                }
            ),
            flush=True,
        )
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--chat", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=120)
    args = parser.parse_args()
    asyncio.run(trial(load(args.config), args.chat, args.directory, args.seconds))


if __name__ == "__main__":
    main()
