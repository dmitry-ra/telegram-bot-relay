"""Audited custom emoji inspection with optional thumbnail archiving."""

import asyncio
from typing import Annotated, Literal
from uuid import uuid4

from aiogram.exceptions import TelegramAPIError
from pydantic import Field, JsonValue

from telegram_bot_relay.config import Config, StrictModel
from telegram_bot_relay.journal.writer import Journal, sync_directory
from telegram_bot_relay.telegram.client import Client

EmojiId = Annotated[str, Field(pattern=r"^[0-9]+$", min_length=1, max_length=64)]
REACTION_METADATA_TIMEOUT = 3.0


class InspectCustomEmoji(StrictModel):
    custom_emoji_ids: list[EmojiId] = Field(min_length=1, max_length=200)
    download_thumbnail: bool = False


class Inspection(StrictModel):
    inspection_id: str
    status: Literal["ok", "failed"]
    stickers: list[dict[str, JsonValue]] = Field(default_factory=list)
    missing_ids: list[str] = Field(default_factory=list)
    error: str | None = None


async def lookup_custom_emoji(
    client: Client, ids: list[str]
) -> tuple[list[dict[str, JsonValue]], list[str]]:
    """Keep complete metadata for requested IDs in request order only."""
    identities = list(dict.fromkeys(ids))
    raw = await client.custom_emoji(identities)
    found: dict[str, dict[str, JsonValue]] = {}
    for value in raw:
        if isinstance(value, dict):
            identity = value.get("custom_emoji_id")
            if isinstance(identity, str) and identity in identities:
                found[identity] = dict(value)
    return (
        [found[identity] for identity in identities if identity in found],
        [identity for identity in identities if identity not in found],
    )


async def prepare_reaction_metadata(
    original: dict[str, JsonValue], client: Client, allowed: bool
) -> dict[str, JsonValue]:
    """Best-effort metadata for authorized custom reaction Set and Clear events."""
    event = original.get("message_reaction")
    if not allowed or not isinstance(event, dict):
        return {}
    raw_ids: list[JsonValue] = []
    for field in ("old_reaction", "new_reaction"):
        reactions = event.get(field)
        if not isinstance(reactions, list):
            continue
        for reaction in reactions:
            if isinstance(reaction, dict) and reaction.get("type") == "custom_emoji":
                identity = reaction.get("custom_emoji_id")
                if identity not in raw_ids:
                    raw_ids.append(identity)
    if not raw_ids:
        return {}
    ids: list[str] = []
    try:
        query = InspectCustomEmoji.model_validate({"custom_emoji_ids": raw_ids})
        ids = query.custom_emoji_ids
        async with asyncio.timeout(REACTION_METADATA_TIMEOUT):
            stickers, missing = await lookup_custom_emoji(client, ids)
        return {"status": "ok", "stickers": list(stickers), "missing_ids": list(missing)}
    except (TelegramAPIError, TimeoutError, ValueError, OSError) as error:
        return {
            "status": "failed",
            "stickers": [],
            "missing_ids": list(ids),
            "error": type(error).__name__,
        }


async def inspect_custom_emoji(
    request: InspectCustomEmoji,
    config: Config,
    journal: Journal,
    client: Client,
    source: str,
) -> Inspection:
    inspection_id = uuid4().hex
    record = journal.append(
        "inspection_requested",
        {
            "inspection_id": inspection_id,
            "source": source,
            "request": request.model_dump(mode="json"),
        },
    )
    ids = list(dict.fromkeys(request.custom_emoji_ids))
    try:
        deadline = asyncio.get_running_loop().time() + config.attachments.timeout
        async with asyncio.timeout(min(REACTION_METADATA_TIMEOUT, config.attachments.timeout)):
            metadata, missing = await lookup_custom_emoji(client, ids)
        remaining_bytes = config.attachments.max_bytes
        stickers: list[dict[str, JsonValue]] = []
        for sticker in metadata:
            if request.download_thumbnail:
                thumbnail = sticker.get("thumbnail")
                file_id = thumbnail.get("file_id") if isinstance(thumbnail, dict) else None
                download: dict[str, JsonValue] = {"status": "unavailable"}
                if not config.attachments.download:
                    download = {"status": "disabled"}
                elif isinstance(file_id, str) and file_id:
                    folder = journal.root / record.period / "inspections" / inspection_id
                    path = folder / (str(len(stickers)) + ".webp")
                    try:
                        if remaining_bytes <= 0:
                            raise ValueError("Inspection thumbnail byte budget exhausted")
                        if asyncio.get_running_loop().time() >= deadline:
                            raise TimeoutError("Inspection time budget exhausted")
                        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                        async with asyncio.timeout_at(deadline):
                            await client.download(
                                file_id, path, remaining_bytes, config.attachments.timeout
                            )
                        size = path.stat().st_size
                        if size > remaining_bytes:
                            raise ValueError("Inspection thumbnail byte budget exceeded")
                        remaining_bytes -= size
                        path.chmod(0o600)
                        sync_directory(folder)
                        download = {"status": "saved", "path": str(path)}
                    except (TelegramAPIError, TimeoutError, ValueError, OSError) as error:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            remaining_bytes = 0
                        download = {"status": "failed", "error": type(error).__name__}
                sticker["thumbnail_download"] = download
            stickers.append(sticker)
        result = Inspection(
            inspection_id=inspection_id,
            status="ok",
            stickers=stickers,
            missing_ids=missing,
        )
    except (TelegramAPIError, TimeoutError, ValueError, OSError) as error:
        result = Inspection(
            inspection_id=inspection_id, status="failed", error=type(error).__name__
        )
    journal.append("inspection_completed", result.model_dump(mode="json"))
    return result
