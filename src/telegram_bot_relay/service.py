"""Durable ingress, independent attachment preparation and optional forwarding."""

import asyncio
import fcntl
import json
import logging
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import IO
from urllib.parse import quote

import aiohttp
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError, TelegramRetryAfter
from pydantic import JsonValue

from telegram_bot_relay.application.access import authorize
from telegram_bot_relay.application.custom_emoji import prepare_reaction_metadata
from telegram_bot_relay.application.models import SendMessage
from telegram_bot_relay.application.operations import Operations
from telegram_bot_relay.config import Config, secret
from telegram_bot_relay.journal.models import Record
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.telegram.attachments import prepare
from telegram_bot_relay.telegram.client import Client
from telegram_bot_relay.telegram.denied import denial_notifications
from telegram_bot_relay.telegram.preview import format_preview

logger = logging.getLogger(__name__)
GATEWAY_CONTENT_BYTES = 65536


def content_size(value: dict[str, JsonValue]) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


class GatewayResponseError(ValueError):
    """The remote response did not confirm queue acceptance."""


class Service:
    def __init__(self, config: Config, journal: Journal, client: Client) -> None:
        self.config, self.journal, self.client = config, journal, client
        self.operations = Operations(config, journal, client)
        self.offset: int | None = None
        self.recent: deque[int] = deque(maxlen=1024)
        self.prepared = 0
        self.gateway_cursor = 0
        self.gateway_initialized = False
        self.gateway_start = 0
        self.bot_id: int | None = None
        self.archive_bot: int | None = None
        self.bot_lock: IO[str] | None = None
        self._restore()

    def _restore(self) -> None:
        gateway = self.config.gateway
        for record in self.journal.records():
            bot_id = record.data.get("bot_id")
            if isinstance(bot_id, int):
                if self.archive_bot is not None and self.archive_bot != bot_id:
                    raise ValueError("Archive contains multiple bot identities")
                self.archive_bot = bot_id
            if record.type == "message_received":
                original = record.data["original"]
                if isinstance(original, dict) and type(original.get("update_id")) is int:
                    update_id = original["update_id"]
                    assert isinstance(update_id, int)
                    self.offset = update_id + 1
                    self.recent.append(update_id)
            elif record.type == "message_prepared":
                self.prepared = int(str(record.data["source_seq"]))
            elif gateway and record.data.get("destination") == gateway.url + "/" + gateway.target:
                if record.type in ("gateway_started", "gateway_accepted"):
                    self.gateway_initialized = True
                    self.gateway_cursor = int(str(record.data["cursor"]))
                    if record.type == "gateway_started":
                        self.gateway_start = self.gateway_cursor

    def ingest(self, original: dict[str, JsonValue]) -> int | None:
        update_id = original.get("update_id")
        if type(update_id) is int and update_id in self.recent:
            return None
        decision = authorize(original, self.config.access)
        record = self.journal.append(
            "message_received",
            {
                "original": original,
                "access": decision.model_dump(mode="json"),
                "bot_id": self.bot_id,
            },
        )
        # Journal.append completes fsync before the next getUpdates offset advances.
        if type(update_id) is int:
            assert isinstance(update_id, int)
            self.offset = update_id + 1
            self.recent.append(update_id)
        if not decision.allowed:
            logger.info(
                "access_denied bot=%s seq=%s update=%s reason=%s",
                self.bot_id,
                record.seq,
                update_id,
                decision.reason,
            )
        return record.seq

    async def process_once(self) -> bool:
        for record in self.journal.records(self.prepared):
            if record.type != "message_received":
                self.prepared = record.seq
                continue
            original, access = record.data["original"], record.data["access"]
            assert isinstance(original, dict) and isinstance(access, dict)
            allowed = access["allowed"] is True
            assets = await prepare(
                self.client,
                original,
                self.journal.root / record.period / "attachments",
                self.config.attachments,
                allowed,
            )
            reaction_metadata = await prepare_reaction_metadata(original, self.client, allowed)
            self.journal.append(
                "message_prepared",
                {
                    "source_seq": record.seq,
                    "source_period": record.period,
                    "access": access,
                    "attachments": list(assets),
                    "reaction_metadata": reaction_metadata,
                },
            )
            self.prepared = record.seq
            if not allowed:
                texts = denial_notifications(
                    original, self.bot_id, record.timestamp, access, record.seq
                )
                for chat in self.config.notify_chats:
                    for text in texts:
                        await self.operations.execute(
                            SendMessage(chat_id=chat, text=text), "notification"
                        )
            return True
        return False

    async def forward_once(self) -> bool:
        settings = self.config.gateway
        if settings is None:
            return False
        destination = settings.url + "/" + settings.target
        if not self.gateway_initialized:
            self.gateway_cursor = self.journal.seq if settings.start == "latest" else 0
            self.gateway_start = self.gateway_cursor
            self.journal.append(
                "gateway_started", {"destination": destination, "cursor": self.gateway_cursor}
            )
            self.gateway_initialized = True
        for prepared in self.journal.records(self.gateway_cursor):
            if prepared.type != "message_prepared":
                self.gateway_cursor = prepared.seq
                continue
            access = prepared.data["access"]
            assert isinstance(access, dict)
            if (
                access["allowed"] is not True
                or int(str(prepared.data["source_seq"])) <= self.gateway_start
            ):
                self.gateway_cursor = prepared.seq
                continue
            source = self.journal.find(int(str(prepared.data["source_seq"])))
            original = source.data["original"]
            assert isinstance(original, dict)
            event = next(
                (v for k, v in original.items() if k != "update_id" and isinstance(v, dict)), {}
            )
            assert isinstance(event, dict)
            sender = event.get("from", {})
            if not sender and "old_reaction" in event and "new_reaction" in event:
                sender = event.get("user", {})
            sender = sender if isinstance(sender, dict) else {}
            downloads: list[JsonValue] = []
            assets = prepared.data["attachments"]
            assert isinstance(assets, list)
            for asset in assets:
                assert isinstance(asset, dict)
                item = dict(asset)
                if item.get("status") == "saved":
                    item["path"] = str(self.journal.root / source.period / str(item["path"]))
                downloads.append(item)
            sent_at = event.get("date")
            metadata = prepared.data.get("reaction_metadata", {})
            metadata = metadata if isinstance(metadata, dict) else {}
            content = self._gateway_content(original, downloads, prepared, metadata)
            message = {
                "channel": "telegram",
                "content": content,
                "sender": str(
                    sender.get("username")
                    or sender.get("first_name")
                    or sender.get("id")
                    or "telegram"
                ),
                "preview": format_preview(event, metadata),
                "sent_at": (
                    datetime.fromtimestamp(sent_at, UTC).isoformat()
                    if isinstance(sent_at, int) and not isinstance(sent_at, bool)
                    else source.timestamp
                ),
            }
            headers = (
                {"Authorization": "Bearer " + secret(settings.token_file)}
                if settings.token_file
                else {}
            )
            async with aiohttp.ClientSession(
                headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.post(
                    settings.url.rstrip("/")
                    + "/v1/targets/"
                    + quote(settings.target, safe="")
                    + "/messages",
                    json=message,
                ) as response:
                    response.raise_for_status()
                    try:
                        result = await response.json()
                    except ValueError:
                        raise GatewayResponseError("Invalid JSON response") from None
                    if not isinstance(result, dict) or not isinstance(result.get("id"), str):
                        raise GatewayResponseError("Missing acceptance ID")
            self.journal.append(
                "gateway_accepted",
                {
                    "destination": destination,
                    "cursor": prepared.seq,
                    "source_seq": source.seq,
                    "gateway_id": result.get("id"),
                },
            )
            self.gateway_cursor = prepared.seq
            return True
        return False

    def _gateway_content(
        self,
        original: dict[str, JsonValue],
        downloads: list[JsonValue],
        prepared: Record,
        metadata: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        content: dict[str, JsonValue] = {**original, "downloads": downloads}
        if not metadata:
            return content
        enriched: dict[str, JsonValue] = {**content, "reaction_metadata": metadata}
        if (
            content_size(content) > GATEWAY_CONTENT_BYTES
            or content_size(enriched) <= GATEWAY_CONTENT_BYTES
        ):
            return enriched
        reference: dict[str, JsonValue] = {
            **content,
            "reaction_metadata": {
                "status": "referenced",
                "journal_path": str(self.journal.root / prepared.period / "events.jsonl"),
                "record_seq": prepared.seq,
            },
        }
        return reference if content_size(reference) <= GATEWAY_CONTENT_BYTES else content

    async def poll(self) -> None:
        while True:
            try:
                updates = await self.client.updates(self.offset)
            except TelegramConflictError:
                raise RuntimeError("Another Telegram receiver is using this bot") from None
            except TelegramRetryAfter as error:
                await asyncio.sleep(error.retry_after)
                continue
            except TelegramNetworkError:
                logger.warning("telegram_poll_network_error")
                await asyncio.sleep(2)
                continue
            for update in updates:
                self.ingest(update)

    async def processing(self) -> None:
        while True:
            if not await self.process_once():
                await asyncio.sleep(0.1)

    async def forwarding(self) -> None:
        while True:
            try:
                if not await self.forward_once():
                    await asyncio.sleep(0.25)
            except (aiohttp.ClientError, TimeoutError, GatewayResponseError):
                logger.warning("gateway_delivery_error")
                await asyncio.sleep(2)

    async def initialize(self) -> None:
        if self.bot_lock is not None and not self.bot_lock.closed:
            return
        bot_id = await self.client.identity()
        if self.archive_bot is not None and self.archive_bot != bot_id:
            raise ValueError("Archive belongs to a different bot")
        root = Path.home() / ".local/state/telegram-bot-relay/locks"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (root / f"{bot_id}.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.journal.append("bot_started", {"bot_id": bot_id})
        except BaseException:
            lock.close()
            raise
        self.bot_lock = lock
        self.bot_id = bot_id

    def close(self) -> None:
        if self.bot_lock is not None:
            self.bot_lock.close()

    async def run(self) -> None:
        await self.initialize()
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.poll())
                tasks.create_task(self.processing())
                if self.config.gateway:
                    tasks.create_task(self.forwarding())
        finally:
            self.close()
