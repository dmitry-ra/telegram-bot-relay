from pathlib import Path

import pytest
from pydantic import ValidationError

from telegram_bot_relay.application.access import authorize
from telegram_bot_relay.application.models import operation_adapter
from telegram_bot_relay.config import Access, Config, load, secret
from tests.helpers import message


@pytest.mark.parametrize("allowed", [True, False])
def test_user_access(allowed: bool) -> None:
    result = authorize(message(user_id=101 if allowed else 202), Access(allowed_users=[101]))
    assert result.allowed is allowed
    assert result.reason == (None if allowed else "user_not_allowed")


def test_empty_public_group_and_identity_policy() -> None:
    raw = message()
    assert not authorize(raw, Access()).allowed
    assert authorize(raw, Access(public=True)).allowed
    event = raw["message"]
    assert isinstance(event, dict)
    event["chat"] = {"id": -100, "type": "supergroup"}
    assert authorize(raw, Access(public=True)).reason == "chat_type_not_allowed"
    assert authorize(raw, Access(public=True, groups=True)).reason == "group_not_allowed"
    assert authorize(raw, Access(public=True, groups=True, allowed_chats=[-100])).allowed
    assert (
        authorize(raw, Access(public=True, groups=True, allowed_chats=[-200])).reason
        == "chat_not_allowed"
    )
    event["sender_chat"] = {"id": -100}
    assert authorize(raw, Access(public=True)).reason == "anonymous_sender"
    event.pop("from")
    assert authorize(raw, Access(public=True)).reason == "missing_sender"
    assert not authorize({"update_id": 10}, Access()).allowed


@pytest.mark.parametrize(
    "body",
    [
        {"method": "send_message", "chat_id": "101", "text": "x"},
        {"method": "send_message", "chat_id": True, "text": "x"},
        {"method": "send_message", "chat_id": 101, "text": "x", "extra": 1},
        {"method": "send_media", "chat_id": 101, "media": [{"kind": "photo"}]},
        {
            "method": "send_media",
            "chat_id": 101,
            "media": [{"kind": "photo", "path": "a", "file_id": "b"}],
        },
        {
            "method": "send_media",
            "chat_id": 101,
            "media": [{"kind": "voice", "file_id": "a"}, {"kind": "photo", "file_id": "b"}],
        },
    ],
)
def test_strict_operations(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        operation_adapter.validate_python(body)


def test_config_validation(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('token_file="token"\napi_token_file="api"\ndata_dir="archive"\n')
    assert load(path).gateway is None
    value = load(path).model_dump()
    changes: list[dict[str, object]] = [
        {"rotate": "* * * * * *"},
        {"timezone": "bad/timezone"},
        {"notify_chats": [999]},
    ]
    for change in changes:
        with pytest.raises((ValueError, KeyError)):
            Config.model_validate({**value, **change})
    (tmp_path / "secret").write_text(" token-value \n")
    assert secret(str(tmp_path / "secret")) == "token-value"
    (tmp_path / "secret").write_text("")
    with pytest.raises(ValueError):
        secret(str(tmp_path / "secret"))
