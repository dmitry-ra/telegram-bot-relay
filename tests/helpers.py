"""Small transport fakes; production models and journal remain real."""

from pathlib import Path

from pydantic import JsonValue

from telegram_bot_relay.application.models import Operation
from telegram_bot_relay.config import Access, Config
from telegram_bot_relay.telegram.client import Client


def configuration(root: Path) -> Config:
    return Config(
        token_file=str(root / "token"),
        api_token_file=str(root / "api-token"),
        data_dir=str(root / "archive"),
        access=Access(allowed_users=[101], outgoing_chats=[101]),
    )


def message(update_id: int = 1, user_id: int = 101) -> dict[str, JsonValue]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1793487600,
            "from": {"id": user_id, "is_bot": False, "first_name": "Example"},
            "chat": {"id": user_id, "type": "private"},
            "text": "Hello \U0001f600",
        },
    }


class FakeClient(Client):
    def __init__(self) -> None:
        self.sent: list[Operation] = []
        self.downloaded: list[str] = []
        self.failure: Exception | None = None

    async def execute(self, operation: Operation) -> JsonValue:
        if self.failure:
            raise self.failure
        self.sent.append(operation)
        return {"message_id": len(self.sent), "chat": {"id": operation.chat_id}}

    async def identity(self) -> int:
        return 123456

    async def download(
        self, file_id: str, path: Path, limit: int, timeout: int
    ) -> dict[str, JsonValue] | None:
        if self.failure:
            raise self.failure
        self.downloaded.append(file_id)
        path.write_bytes(b"example file")
        return None

    async def close(self) -> None:
        pass
