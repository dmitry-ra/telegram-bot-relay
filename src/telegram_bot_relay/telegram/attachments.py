"""Preserve file descriptors and download without interpreting their content."""

import asyncio
import os
import re
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue

from telegram_bot_relay.config import Attachments
from telegram_bot_relay.journal.writer import private_directory, sync_directory
from telegram_bot_relay.telegram.client import Client


def descriptors(value: JsonValue) -> list[dict[str, JsonValue]]:
    found: dict[str, dict[str, JsonValue]] = {}

    def visit(item: JsonValue) -> None:
        if isinstance(item, dict):
            file_id = item.get("file_id")
            if isinstance(file_id, str):
                found[file_id] = item
            else:
                for child in item.values():
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return list(found.values())


async def prepare(
    client: Client,
    original: dict[str, JsonValue],
    directory: Path,
    settings: Attachments,
    allowed: bool,
) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    for item in descriptors(original):
        entry: dict[str, JsonValue] = {"file_id": item["file_id"]}
        if not settings.download or (not allowed and not settings.download_denied):
            entry["status"] = "skipped_policy"
        else:
            size = item.get("file_size")
            if isinstance(size, int) and size > settings.max_bytes:
                entry["status"] = "size_limited"
                result.append(entry)
                continue
            directory.mkdir(exist_ok=True, mode=0o700)
            private_directory(directory)
            suffix = Path(str(item.get("file_name", "file.bin"))).suffix
            suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix) else ".bin"
            path = directory / (uuid4().hex + suffix)
            temporary = path.with_suffix(path.suffix + ".part")
            try:
                await asyncio.wait_for(
                    client.download(
                        str(item["file_id"]), temporary, settings.max_bytes, settings.timeout
                    ),
                    timeout=settings.timeout,
                )
                os.replace(temporary, path)
                sync_directory(directory)
                entry.update(
                    status="saved",
                    path=str(path.relative_to(directory.parent)),
                    bytes=path.stat().st_size,
                )
            except Exception as error:
                temporary.unlink(missing_ok=True)
                entry.update(status="failed", error=type(error).__name__)
        result.append(entry)
    return result
