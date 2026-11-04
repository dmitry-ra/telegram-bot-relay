import asyncio
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage as ApiSendMessage

from telegram_bot_relay.application.models import Callback, Media, SendMedia, SendMessage, Transfer
from telegram_bot_relay.application.operations import Operations
from telegram_bot_relay.journal.writer import Journal
from tests.helpers import FakeClient, configuration


@pytest.mark.parametrize("status", ["sent", "denied", "failed", "uncertain"])
def test_outbound_audit_statuses(tmp_path: Path, status: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = FakeClient()
        if status == "failed":
            client.failure = TelegramBadRequest(ApiSendMessage(chat_id=101, text="x"), "bad")
        if status == "uncertain":
            client.failure = TelegramNetworkError(ApiSendMessage(chat_id=101, text="x"), "timeout")
        result = await Operations(config, journal, client).execute(
            SendMessage(chat_id=202 if status == "denied" else 101, text="x"), "mcp"
        )
        assert result.status == status
        records = list(journal.records())
        assert records[-2].type == "operation_requested"
        assert records[-1].type == "operation_completed"
        assert records[-2].data["operation_id"] == records[-1].data["operation_id"]
        journal.close()

    asyncio.run(run())


def test_callback_requires_authorized_original_context(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = FakeClient()
        operations = Operations(config, journal, client)
        request = Callback(chat_id=101, callback_query_id="one")
        assert (await operations.execute(request, "http")).status == "denied"
        journal.append(
            "message_received",
            {
                "original": {
                    "update_id": 1,
                    "callback_query": {
                        "id": "one",
                        "from": {"id": 202},
                        "message": {"chat": {"id": 101, "type": "private"}},
                    },
                }
            },
        )
        assert (await operations.execute(request, "http")).status == "denied"
        request = Callback(chat_id=101, callback_query_id="two")
        journal.append(
            "message_received",
            {
                "original": {
                    "update_id": 2,
                    "callback_query": {
                        "id": "two",
                        "from": {"id": 101},
                        "message": {"chat": {"id": 101, "type": "private"}},
                    },
                }
            },
        )
        assert (await operations.execute(request, "http")).status == "sent"
        journal.close()

    asyncio.run(run())


def test_upload_snapshot_and_transfer_policy(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config.attachments.upload_roots = [str(tmp_path / "uploads")]
        (tmp_path / "uploads").mkdir()
        source = tmp_path / "uploads/photo.png"
        source.write_bytes(b"original")
        journal = Journal(Path(config.data_dir))
        client = FakeClient()
        operations = Operations(config, journal, client)
        result = await operations.execute(
            SendMedia(chat_id=101, media=[Media(kind="document", path=str(source))]), "http"
        )
        assert result.status == "sent"
        sent = client.sent[-1]
        assert isinstance(sent, SendMedia) and sent.media[0].path
        saved = Path(sent.media[0].path)
        assert saved.name == source.name
        assert saved.parent.name.startswith("out-")
        source.write_bytes(b"changed")
        assert saved.read_bytes() == b"original"
        outside = tmp_path / "outside"
        outside.write_bytes(b"x")
        result = await operations.execute(
            SendMedia(chat_id=101, media=[Media(kind="photo", path=str(outside))]), "http"
        )
        assert result.status == "failed"
        result = await operations.execute(
            Transfer(method="forward_message", chat_id=101, from_chat_id=202, message_id=1), "http"
        )
        assert result.status == "denied"
        journal.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("groups", "allowed_chats", "status"),
    [(False, [-202], "denied"), (True, [], "denied"), (True, [-202], "sent")],
)
def test_callback_authorizes_nested_group_context(
    tmp_path: Path, groups: bool, allowed_chats: list[int], status: str
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config.access.groups = groups
        config.access.allowed_chats = allowed_chats
        config.access.outgoing_chats = [-202]
        journal = Journal(Path(config.data_dir))
        journal.append(
            "message_received",
            {
                "original": {
                    "callback_query": {
                        "id": "group",
                        "from": {"id": 101},
                        "message": {
                            "chat": {"id": -202, "type": "supergroup"},
                            "sender_chat": {"id": -202},
                        },
                    }
                }
            },
        )
        client = FakeClient()
        result = await Operations(config, journal, client).execute(
            Callback(chat_id=-202, callback_query_id="group"), "http"
        )
        assert result.status == status
        assert len(client.sent) == (1 if status == "sent" else 0)
        journal.close()

    asyncio.run(run())
