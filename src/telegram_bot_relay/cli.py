"""Start one bot daemon with REST and Streamable HTTP MCP."""

import argparse
import asyncio
import logging
import os
from pathlib import Path

import uvicorn

from telegram_bot_relay import __version__
from telegram_bot_relay.config import Config, load, secret
from telegram_bot_relay.interfaces.server import create_app
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import Service
from telegram_bot_relay.telegram.client import Client


async def serve(config: Config) -> None:
    journal = Journal(Path(config.data_dir).expanduser(), config.rotate, config.timezone)
    client: Client | None = None
    service: Service | None = None
    try:
        client = Client(secret(config.token_file))
        service = Service(config, journal, client)
        await service.initialize()
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
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            server.should_exit = True
            tasks[0].cancel()
            try:
                await asyncio.wait_for(asyncio.shield(tasks[1]), 5)
            except TimeoutError:
                tasks[1].cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if service is not None:
            service.close()
        if client is not None:
            await client.close()
        journal.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive and relay Telegram bot messages")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(serve(load(args.config)))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        # SDK and validation exceptions may contain secrets or source content.
        parser.exit(1, f"Relay stopped: {type(error).__name__}\n")


if __name__ == "__main__":
    main()
