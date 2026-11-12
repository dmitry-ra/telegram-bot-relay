"""Contracts shared by REST, MCP and operation logging."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from telegram_bot_relay.config import StrictModel


class Address(StrictModel):
    chat_id: int


class ThreadAddress(Address):
    thread_id: int | None = None


class SendMessage(ThreadAddress):
    method: Literal["send_message"] = "send_message"
    text: str = Field(min_length=1, max_length=4096)
    reply_to: int | None = None
    parse_mode: Literal["HTML", "MarkdownV2"] | None = None


class ReplyMessage(SendMessage):
    reply_to: int


class Media(StrictModel):
    kind: Literal[
        "photo", "document", "voice", "audio", "video", "video_note", "animation", "sticker"
    ]
    file_id: str | None = None
    path: str | None = None
    caption: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def one_source(self) -> "Media":
        if bool(self.file_id) == bool(self.path):
            raise ValueError("Exactly one of file_id and path is required")
        if self.caption is not None and self.kind in ("sticker", "video_note"):
            raise ValueError("This media kind has no caption")
        return self


class SendMedia(ThreadAddress):
    method: Literal["send_media"] = "send_media"
    media: list[Media] = Field(min_length=1, max_length=10)
    reply_to: int | None = None

    @model_validator(mode="after")
    def album_types(self) -> "SendMedia":
        if len(self.media) > 1:
            kinds = {m.kind for m in self.media}
            if not (kinds <= {"photo", "video"} or kinds == {"document"} or kinds == {"audio"}):
                raise ValueError("Album must be photos/videos, documents, or audio")
        return self


class Transfer(ThreadAddress):
    method: Literal["forward_message", "copy_message"]
    from_chat_id: int
    message_id: int


class EditMessage(Address):
    method: Literal["edit_message"] = "edit_message"
    message_id: int
    text: str = Field(min_length=1, max_length=4096)
    parse_mode: Literal["HTML", "MarkdownV2"] | None = None


class DeleteMessage(Address):
    method: Literal["delete_message"] = "delete_message"
    message_id: int = Field(gt=0)


class Reaction(Address):
    method: Literal["set_reaction"] = "set_reaction"
    message_id: int
    emoji: str = Field(min_length=1, max_length=32)


class Callback(StrictModel):
    method: Literal["answer_callback"] = "answer_callback"
    chat_id: int
    callback_query_id: str = Field(min_length=1)
    text: str = Field(default="", max_length=200)


Operation = Annotated[
    SendMessage | SendMedia | Transfer | EditMessage | DeleteMessage | Reaction | Callback,
    Field(discriminator="method"),
]
operation_adapter: TypeAdapter[Operation] = TypeAdapter(Operation)


class Outcome(StrictModel):
    operation_id: str
    status: Literal["sent", "denied", "failed", "uncertain"]
    result: JsonValue = None
    error: str | None = None
