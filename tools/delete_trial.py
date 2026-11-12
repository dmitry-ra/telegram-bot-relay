"""Explicit live deletion acceptance against an already running Relay instance."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from telegram_bot_relay.application.models import Outcome
from telegram_bot_relay.config import load, secret


async def trial(config_path: Path, chat: int, directory: Path) -> None:
    config = load(config_path)
    if chat not in config.access.outgoing_chats:
        raise ValueError("Test chat must be an allowed outgoing destination")
    directory.mkdir(mode=0o700)

    def save(name: str, value: Any) -> None:
        with (directory / name).open("x") as output:
            json.dump(value, output, indent=2)
            output.flush()
            os.fsync(output.fileno())

    save("intent.json", {"chat_id": chat, "only_new_test_messages": True, "no_replay": True})
    url = f"http://127.0.0.1:{config.port}"
    async with create_mcp_http_client(
        headers={"Authorization": "Bearer " + secret(config.api_token_file)}
    ) as http:
        health = await http.get(url + "/healthz")
        health.raise_for_status()
        save("health.json", health.json())
        async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tool = next(
                    t for t in (await session.list_tools()).tools if t.name == "delete_message"
                )
                if tool.annotations is None or tool.annotations.destructive_hint is not True:
                    raise ValueError("Expected destructive deletion tool")
                save("delete-tool.json", tool.model_dump(mode="json"))

                async def operation(surface: str, name: str, request: dict[str, Any]) -> Outcome:
                    save(name + "-intent.json", {"surface": surface, "request": request})
                    if surface == "http":
                        response = await http.post(url + "/v1/operations", json=request)
                        response.raise_for_status()
                        value = response.json()
                    else:
                        result = await session.call_tool(request["method"], {"request": request})
                        if result.is_error or result.structured_content is None:
                            raise RuntimeError("MCP outcome unavailable; do not replay")
                        value = result.structured_content
                    outcome = Outcome.model_validate(value)
                    save(name + "-result.json", outcome.model_dump(mode="json"))
                    if outcome.status != "sent":
                        raise RuntimeError(
                            "Operation not confirmed; inspect receipts, do not replay"
                        )
                    return outcome

                deleted = []
                for surface in ("http", "mcp"):
                    sent = await operation(
                        surface,
                        surface + "-send",
                        {
                            "method": "send_message",
                            "chat_id": chat,
                            "text": f"Relay deletion test ({surface.upper()}); this test message will be deleted.",
                        },
                    )
                    message = sent.result
                    if not isinstance(message, dict):
                        raise ValueError("Expected a newly created test message")
                    message_id, destination = message.get("message_id"), message.get("chat")
                    if (
                        type(message_id) is not int
                        or message_id <= 0
                        or not isinstance(destination, dict)
                        or destination.get("id") != chat
                    ):
                        raise ValueError("Test message identity unconfirmed; no deletion")
                    removed = await operation(
                        surface,
                        surface + "-delete",
                        {
                            "method": "delete_message",
                            "chat_id": chat,
                            "message_id": message_id,
                        },
                    )
                    if removed.result is not True:
                        raise ValueError("Deletion not confirmed; do not replay")
                    deleted.append(
                        {
                            "surface": surface,
                            "message_id": message_id,
                            "operation_id": removed.operation_id,
                        }
                    )
                report = {"chat_id": chat, "deleted_test_messages": deleted, "status": "passed"}
                save("report.json", report)
                print(json.dumps(report))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--chat", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    asyncio.run(trial(args.config, args.chat, args.directory))


if __name__ == "__main__":
    main()
