"""One implementation of outbound policy, snapshots, audit and Telegram calls."""

import os
import shutil
from pathlib import Path
from uuid import uuid4

from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from pydantic import JsonValue

from telegram_bot_relay.application.access import authorize
from telegram_bot_relay.application.models import Callback, Operation, Outcome, SendMedia, Transfer
from telegram_bot_relay.config import Config
from telegram_bot_relay.journal.writer import Journal, sync_directory
from telegram_bot_relay.telegram.client import Client


class Operations:
    def __init__(self, config: Config, journal: Journal, client: Client) -> None:
        self.config, self.journal, self.client = config, journal, client
        pending: set[str] = set()
        for record in journal.records():
            operation_id = record.data.get("operation_id")
            if not isinstance(operation_id, str):
                continue
            if record.type == "operation_requested":
                pending.add(operation_id)
            elif record.type == "operation_completed":
                pending.discard(operation_id)
        for operation_id in sorted(pending):
            journal.append(
                "operation_completed",
                Outcome(
                    operation_id=operation_id,
                    status="uncertain",
                    error="process_restarted",
                ).model_dump(mode="json"),
            )

    async def execute(self, operation: Operation, source: str) -> Outcome:
        operation_id = uuid4().hex
        requested = self.journal.append(
            "operation_requested",
            {
                "operation_id": operation_id,
                "source": source,
                "request": operation.model_dump(mode="json"),
            },
        )
        allowed = operation.chat_id in self.config.access.outgoing_chats
        if isinstance(operation, Transfer):
            allowed = allowed and operation.from_chat_id in self.config.access.outgoing_chats
        if isinstance(operation, Callback):
            allowed = allowed and self._callback_allowed(operation)
        if not allowed:
            outcome = Outcome(operation_id=operation_id, status="denied", error="chat_not_allowed")
        else:
            try:
                operation = self._snapshot(operation, requested.period, operation_id)
                outcome = Outcome(
                    operation_id=operation_id,
                    status="sent",
                    result=await self.client.execute(operation),
                )
            except (TelegramNetworkError, TimeoutError):
                outcome = Outcome(
                    operation_id=operation_id, status="uncertain", error="telegram_network_error"
                )
            except (TelegramAPIError, ValueError, OSError) as error:
                outcome = Outcome(
                    operation_id=operation_id, status="failed", error=type(error).__name__
                )
        self.journal.append("operation_completed", outcome.model_dump(mode="json"))
        return outcome

    def _callback_allowed(self, operation: Callback) -> bool:
        for record in self.journal.records():
            original = record.data.get("original")
            if record.type != "message_received" or not isinstance(original, dict):
                continue
            callback = original.get("callback_query")
            if not isinstance(callback, dict) or callback.get("id") != operation.callback_query_id:
                continue
            context = callback.get("message")
            chat = context.get("chat") if isinstance(context, dict) else None
            sender = callback.get("from")
            target = (
                chat.get("id")
                if isinstance(chat, dict)
                else sender.get("id") if isinstance(sender, dict) else None
            )
            return target == operation.chat_id and authorize(original, self.config.access).allowed
        return False

    def _snapshot(self, operation: Operation, period: str, operation_id: str) -> Operation:
        if not isinstance(operation, SendMedia):
            return operation
        copied = operation.model_copy(deep=True)
        artifacts: list[JsonValue] = []
        for index, media in enumerate(copied.media):
            if media.path is None:
                continue
            source = Path(media.path).resolve(strict=True)
            roots = [Path(p).expanduser().resolve() for p in self.config.attachments.upload_roots]
            if not any(source.is_relative_to(root) for root in roots):
                raise ValueError("Upload path outside allowed roots")
            if not source.is_file() or source.stat().st_size > self.config.attachments.max_bytes:
                raise ValueError("Upload size limit")
            directory = self.journal.root / period / "attachments"
            directory.mkdir(exist_ok=True, mode=0o700)
            snapshot = directory / f"out-{operation_id}-{index}"
            snapshot.mkdir(mode=0o700)
            destination = snapshot / source.name
            shutil.copyfile(source, destination)
            with destination.open("rb") as stream:
                os.fsync(stream.fileno())
            sync_directory(snapshot)
            sync_directory(directory)
            media.path = str(destination)
            artifacts.append(
                {"period": period, "path": str(destination.relative_to(directory.parent))}
            )
        if artifacts:
            # Persist snapshots before the network side effect.
            self.journal.append(
                "operation_artifacts",
                {
                    "operation_id": operation_id,
                    "source": "artifact_snapshot",
                    "artifacts": artifacts,
                },
            )
        return copied
