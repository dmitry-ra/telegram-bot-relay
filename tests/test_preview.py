from copy import deepcopy
from textwrap import shorten

import pytest
from pydantic import JsonValue

from telegram_bot_relay.telegram.preview import format_preview


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            {"text": "Hello \u041f\u0440\u0438\u0432\u0435\u0442 \u4e16\u754c \U0001f600"},
            "Hello \u041f\u0440\u0438\u0432\u0435\u0442 \u4e16\u754c \U0001f600",
        ),
        ({"text": "a\n\t b\x1b c"}, "a b c"),
        ({"photo": [{"file_id": "small"}, {"file_id": "large"}]}, "\U0001f5bc\ufe0f Photo"),
        ({"photo": [], "caption": "View"}, "\U0001f5bc\ufe0f Photo | View"),
        (
            {"document": {"file_name": "report.pdf"}, "caption": "Read this"},
            "\U0001f4ce Document | report.pdf | Read this",
        ),
        ({"voice": {"duration": 4}}, "\U0001f3a4 Voice | 4s"),
        ({"audio": {"duration": 0, "title": "Song"}}, "\U0001f3b5 Audio | 0s | Song"),
        ({"video": {"duration": 12}}, "\U0001f3ac Video | 12s"),
        ({"animation": {"file_name": "clip.gif"}}, "\U0001f39e\ufe0f Animation | clip.gif"),
        ({"video_note": {"duration": 5}}, "\U0001f4f9 Video note | 5s"),
        ({"sticker": {"emoji": "\U0001f600"}}, "\U0001f3f7\ufe0f Sticker | \U0001f600"),
        ({"contact": {}}, "\U0001f464 Contact"),
        ({"location": {}}, "\U0001f4cd Location"),
        ({"venue": {"title": "Park"}}, "\U0001f4cd Venue | Park"),
        ({"poll": {"question": "Which?"}}, "\U0001f4ca Poll | Which?"),
        ({"dice": {"emoji": "\U0001f3b2"}}, "\U0001f3b2 Dice | \U0001f3b2"),
        (
            {"forward_origin": {"type": "hidden_user"}, "text": "Hello"},
            "\u21aa\ufe0f Forward | Hello",
        ),
        ({"data": "opaque"}, "\U0001f518 Button interaction"),
        ({"game_short_name": "game"}, "\U0001f518 Button interaction"),
        ({"future_type": {}}, "\U0001f4e8 Telegram event"),
        ({"text": " \n", "caption": None}, "\U0001f4e8 Telegram event"),
        ({"document": {"file_name": None, "duration": True, "title": 123}}, "\U0001f4ce Document"),
        ({"voice": {"duration": -1}}, "\U0001f3a4 Voice"),
        ({"voice": None}, "\U0001f3a4 Voice"),
    ],
)
def test_preview(event: dict[str, JsonValue], expected: str) -> None:
    before = deepcopy(event)
    assert format_preview(event) == expected
    assert event == before


def test_long_unicode_preview_is_bounded_without_changing_original() -> None:
    text = "\u754c\U0001f600" * 100
    event: dict[str, JsonValue] = {"text": text}
    assert format_preview(event) == text[:157] + "..."
    assert event["text"] == text


def test_specific_media_wins_over_telegram_compatibility_fields() -> None:
    assert format_preview({"animation": {"file_name": "a.gif"}, "document": {}}) == (
        "\U0001f39e\ufe0f Animation | a.gif"
    )
    assert format_preview({"venue": {"title": "Park"}, "location": {}}) == (
        "\U0001f4cd Venue | Park"
    )


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({"reply_to_message": {"message_id": 42, "text": "Parent"}}, '\u21a9 #42 "Parent"'),
        (
            {
                "reply_to_message": {"message_id": 42, "text": "Whole parent"},
                "quote": {"text": "Selected", "is_manual": True},
            },
            '\u21a9 #42 "Selected"',
        ),
        ({"reply_to_message": {"message_id": 42, "caption": "Caption"}}, '\u21a9 #42 "Caption"'),
        (
            {"reply_to_message": {"message_id": 42, "photo": []}},
            '\u21a9 #42 "\U0001f5bc\ufe0f Photo"',
        ),
        ({"reply_to_message": {"message_id": 42}}, "\u21a9 #42"),
        ({"reply_to_message": {}}, "\u21a9"),
        ({"external_reply": {"message_id": 43}}, "\u21a9 #43"),
        ({"quote": {"text": "Selected"}}, '\u21a9 "Selected"'),
        ({"reply_to_message": {"message_id": True}, "quote": {"text": 7}}, "\u21a9"),
        ({"reply_to_message": {"message_id": -1}}, "\u21a9"),
        ({"reply_to_message": {"text": "Parent"}, "quote": {"text": " \n"}}, '\u21a9 "Parent"'),
        ({"reply_to_message": {"text": "a\nb\t\u754c\U0001f600"}}, '\u21a9 "a b \u754c\U0001f600"'),
    ],
)
def test_reply_context(context: dict[str, JsonValue], expected: str) -> None:
    event: dict[str, JsonValue] = {**context, "text": "Current"}
    original = deepcopy(event)
    assert format_preview(event) == expected + " | Current"
    assert "Forward" not in format_preview(event)
    assert event == original


def test_reply_forward_and_media_remain_visible_in_shortened_preview() -> None:
    event: dict[str, JsonValue] = {
        "reply_to_message": {"message_id": 42, "text": "Parent " * 100},
        "quote": {"text": "Selected " * 100},
        "forward_origin": {"type": "channel"},
        "voice": {"duration": 4},
        "caption": "Current " * 100,
    }
    preview = format_preview(event)
    assert len(preview) == 160
    assert '"Selected Selected...' in preview
    assert "Current" in preview
    visible = shorten(preview, width=64, placeholder="...")
    for marker in ["\u21a9 #42", "Forward", "\U0001f3a4 Voice"]:
        assert marker in visible


def test_malformed_reply_fields_do_not_mark_ordinary_text_as_a_reply() -> None:
    assert format_preview({"reply_to_message": None, "quote": [], "text": "Current"}) == "Current"
