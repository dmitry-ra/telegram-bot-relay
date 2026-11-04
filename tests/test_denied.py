import json
from copy import deepcopy

import pytest
from pydantic import JsonValue

from telegram_bot_relay.telegram.denied import denial_notifications
from tests.helpers import message


@pytest.mark.parametrize(
    "original",
    [
        message(user_id=202),
        {
            "update_id": 2,
            "callback_query": {"from": {"id": 203, "username": "Example"}, "data": "x"},
        },
        {"update_id": 3, "poll_answer": {"user": {"id": 204}, "option_ids": [1]}},
        {"update_id": 4, "message": {"sender_chat": {"id": -10, "title": "Channel"}}},
        {"update_id": 5},
        {"update_id": 6, "message": {"from": "malformed"}},
    ],
)
def test_notice_retains_all_original_fields(original: dict[str, JsonValue]) -> None:
    before = deepcopy(original)
    texts = denial_notifications(
        original, 123, "2026-11-02T18:04:35Z", {"reason": "user_not_allowed"}, 42
    )
    assert all(text.startswith("\u26a0\ufe0f Access Denied") for text in texts)
    assert (
        json.loads("".join(text.split("\n\n", 1)[1] for text in texts).split("(JSON):\n", 1)[1])
        == original
    )
    assert original == before
    assert "123" in texts[0] and "42" in texts[0]


def test_long_notice_with_emoji_has_no_truncation_or_markup() -> None:
    original = message(user_id=202)
    event = original["message"]
    assert isinstance(event, dict)
    event["from"] = {
        "id": 202,
        "first_name": "<b>Name</b>",
        "last_name": "Last",
        "username": "login",
    }
    event["text"] = "\U0001f600" * 6000 + "tail"
    texts = denial_notifications(original, 123, "now", {"reason": "user_not_allowed"}, 1)
    assert len(texts) > 1
    assert all(len(text.encode("utf-16-le")) // 2 <= 4096 for text in texts)
    body = "".join(text.split("\n\n", 1)[1] for text in texts)
    assert "@login" in body and "<b>Name</b> Last" in body
    assert json.loads(body.split("(JSON):\n", 1)[1]) == original


def test_generated_labels_are_english_with_only_warning_icon() -> None:
    original = message(user_id=202)
    original["message"] = {"from": {"id": 202}, "text": "test"}
    text = denial_notifications(original, 123, "now", {"reason": "user_not_allowed"}, 42)[0]
    assert text.startswith("\u26a0\ufe0f Access Denied [1/1]\n\n")
    assert text.count("\u26a0\ufe0f") == 1
    assert text.removeprefix("\u26a0\ufe0f").isascii()
    for label in (
        "Bot: 123",
        "Name: not provided",
        "Username: not provided",
        "User ID: 202",
        "Received: now",
        "Reason: user_not_allowed",
        "Journal record: 42",
        "Original event (JSON):",
    ):
        assert label in text


def test_original_language_and_emoji_are_preserved() -> None:
    original = message(user_id=202)
    original["message"] = {
        "from": {"id": 202, "first_name": "\u0412\u0438\u043d\u0441"},
        "text": "\u041f\u0440\u0438\u0432\u0435\u0442 \U0001f600",
    }
    text = denial_notifications(original, 123, "now", {"reason": "user_not_allowed"}, 42)[0]
    assert json.loads(text.split("Original event (JSON):\n", 1)[1]) == original
