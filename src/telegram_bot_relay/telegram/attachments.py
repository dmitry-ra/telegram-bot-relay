"""Preserve file descriptors and download without interpreting their content."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue

from telegram_bot_relay.config import Attachments
from telegram_bot_relay.journal.writer import private_directory, sync_directory
from telegram_bot_relay.telegram.client import Client
from telegram_bot_relay.telegram.file_types import MEDIA_KINDS, hints


def descriptors(value: JsonValue) -> list[dict[str, JsonValue]]:
    return [item for item, _ in located_descriptors(value)]


def located_descriptors(value: JsonValue) -> list[tuple[dict[str, JsonValue], tuple[str, ...]]]:
    found: dict[str, tuple[dict[str, JsonValue], tuple[str, ...]]] = {}

    def visit(item: JsonValue, kind: str = "unknown") -> None:
        if isinstance(item, dict):
            file_id = item.get("file_id")
            if isinstance(file_id, str):
                previous = found.get(file_id)
                kinds = previous[1] if previous else ()
                found[file_id] = (item, kinds if kind in kinds else (*kinds, kind))
            else:
                for key, child in item.items():
                    visit(child, key if key in MEDIA_KINDS else kind)
        elif isinstance(item, list):
            for child in item:
                visit(child, kind)

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
    for item, kinds in located_descriptors(original):
        type_hints = hints(item, kinds)
        entry: dict[str, JsonValue] = {"file_id": item["file_id"], "type_hints": type_hints}
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
            stem = uuid4().hex
            temporary = directory / (stem + ".part")
            try:
                telegram = await asyncio.wait_for(
                    client.download(
                        str(item["file_id"]), temporary, settings.max_bytes, settings.timeout
                    ),
                    timeout=settings.timeout,
                )
                type_hints = hints(item, kinds, telegram)
                path = directory / (stem + str(type_hints["suffix"]))
                os.replace(temporary, path)
                sync_directory(directory)
                entry.update(
                    status="saved",
                    path=str(path.relative_to(directory.parent)),
                    bytes=path.stat().st_size,
                    type_hints=type_hints,
                )
            except Exception as error:
                temporary.unlink(missing_ok=True)
                entry.update(status="failed", error=type(error).__name__)
        result.append(entry)
    return result
