"""Aiogram transport with raw updates retained before SDK message conversion."""

import io
from pathlib import Path
from typing import Any, BinaryIO, ClassVar

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.methods import (
    AnswerCallbackQuery,
    CopyMessage,
    EditMessageText,
    ForwardMessage,
    SendAnimation,
    SendAudio,
    SendDocument,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SendSticker,
    SendVideo,
    SendVideoNote,
    SendVoice,
    SetMessageReaction,
    TelegramMethod,
)
from aiogram.types import FSInputFile
from pydantic import JsonValue, TypeAdapter

from telegram_bot_relay.application.models import Operation, SendMedia, ThreadAddress


class RawUpdates(TelegramMethod[list[dict[str, JsonValue]]]):
    """Only the update envelope is typed: unknown Telegram fields must survive."""

    __api_method__: ClassVar[str] = "getUpdates"
    __returning__: ClassVar[Any] = list[dict[str, JsonValue]]
    offset: int | None = None
    limit: int = 100
    timeout: int = 25
    allowed_updates: list[str]


class LimitedWriter(io.BufferedWriter):
    def __init__(self, raw: BinaryIO, limit: int) -> None:
        super().__init__(raw)
        self.limit = limit
        self.written = 0

    def write(self, data: Any) -> int:
        self.written += len(data)
        if self.written > self.limit:
            raise ValueError("Attachment size limit")
        return super().write(data)


class Client:
    def __init__(self, token: str, api_url: str | None = None) -> None:
        session = AiohttpSession(
            api=(
                TelegramAPIServer.from_base(api_url)
                if api_url
                else TelegramAPIServer.from_base("https://api.telegram.org")
            )
        )
        self.bot = Bot(token, session=session)

    async def identity(self) -> int:
        return (await self.bot.get_me()).id

    async def updates(self, offset: int | None, timeout: int = 25) -> list[dict[str, JsonValue]]:
        from aiogram.types import Update

        return await self.bot(
            RawUpdates(
                offset=offset,
                timeout=timeout,
                allowed_updates=[k for k in Update.model_fields if k != "update_id"],
            )
        )

    async def execute(self, operation: Operation) -> JsonValue:
        params: dict[str, Any] = operation.model_dump(exclude={"method"}, exclude_none=True)
        if isinstance(operation, ThreadAddress):
            thread = params.pop("thread_id", None)
            if thread is not None:
                params["message_thread_id"] = thread
        reply = params.pop("reply_to", None)
        if reply is not None:
            params["reply_parameters"] = {"message_id": reply}
        if isinstance(operation, SendMedia):
            media = []
            for item in operation.media:
                content: dict[str, Any] = {
                    "type": item.kind,
                    "media": FSInputFile(item.path) if item.path else item.file_id,
                }
                if item.caption is not None:
                    content["caption"] = item.caption
                media.append(content)
            params["media"] = media
            if len(media) > 1:
                method: TelegramMethod[Any] = SendMediaGroup.model_validate(params)
            else:
                classes: dict[str, type[TelegramMethod[Any]]] = {
                    "photo": SendPhoto,
                    "document": SendDocument,
                    "voice": SendVoice,
                    "audio": SendAudio,
                    "video": SendVideo,
                    "video_note": SendVideoNote,
                    "animation": SendAnimation,
                    "sticker": SendSticker,
                }
                single = media[0]
                params.pop("media")
                params[single["type"]] = single["media"]
                if "caption" in single:
                    params["caption"] = single["caption"]
                method = classes[single["type"]].model_validate(params)
        else:
            methods: dict[str, type[TelegramMethod[Any]]] = {
                "send_message": SendMessage,
                "forward_message": ForwardMessage,
                "copy_message": CopyMessage,
                "edit_message": EditMessageText,
                "set_reaction": SetMessageReaction,
                "answer_callback": AnswerCallbackQuery,
            }
            if operation.method == "set_reaction":
                params["reaction"] = [{"type": "emoji", "emoji": params.pop("emoji")}]
            if operation.method == "answer_callback":
                params.pop("chat_id")
            method = methods[operation.method].model_validate(params)
        result = await self.bot(method)
        return TypeAdapter(JsonValue).validate_json(
            TypeAdapter(Any).dump_json(result, by_alias=True, exclude_unset=True)
        )

    async def download(self, file_id: str, path: Path, limit: int, timeout: int) -> None:
        file = await self.bot.get_file(file_id)
        if file.file_size is not None and file.file_size > limit:
            raise ValueError("Attachment size limit")
        if file.file_path is None:
            raise ValueError("Attachment path unavailable")
        import os

        with path.open("wb", buffering=0) as raw:
            with LimitedWriter(raw, limit) as output:
                await self.bot.download_file(file.file_path, output, timeout=timeout)
                output.flush()
                os.fsync(output.fileno())

    async def close(self) -> None:
        await self.bot.session.close()
