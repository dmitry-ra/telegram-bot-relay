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


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ([], [{"type": "emoji", "emoji": "\U0001f44d"}], "Set \U0001f44d"),
        (
            [],
            [{"type": "emoji", "emoji": "\u2764\ufe0f\u200d\U0001f525"}],
            "Set \u2764\ufe0f\u200d\U0001f525",
        ),
        ([{"type": "emoji", "emoji": "\U0001f440"}], [], "Clear \U0001f440"),
        (
            [{"type": "emoji", "emoji": "\U0001f44d"}],
            [{"type": "emoji", "emoji": "\U0001f525"}],
            "Set \U0001f525 | Clear \U0001f44d",
        ),
        (
            [{"type": "emoji", "emoji": "\U0001f44d"}],
            [{"type": "emoji", "emoji": "\U0001f44d"}, {"type": "emoji", "emoji": "\U0001f525"}],
            "Set \U0001f525",
        ),
        (
            [{"type": "emoji", "emoji": "\U0001f44d"}, {"type": "emoji", "emoji": "\U0001f525"}],
            [],
            "Clear \U0001f44d \U0001f525",
        ),
        (
            [{"type": "emoji", "emoji": "\U0001f44d"}, {"type": "emoji", "emoji": "\U0001f525"}],
            [{"type": "emoji", "emoji": "\U0001f525"}, {"type": "emoji", "emoji": "\U0001f44d"}],
            "Unchanged",
        ),
        ([], [], "Unchanged"),
        (
            [],
            [{"type": "emoji", "emoji": "\U0001f44d"}, {"type": "emoji", "emoji": "\U0001f44d"}],
            "Set \U0001f44d",
        ),
        ([], [{"type": "custom_emoji", "custom_emoji_id": "1234"}], "Set Custom emoji 1234"),
        ([{"type": "custom_emoji", "custom_emoji_id": "1234"}], [], "Clear Custom emoji 1234"),
        ([], [{"type": "paid"}], "Set Paid \u2b50"),
        ([{"type": "paid"}], [], "Clear Paid \u2b50"),
        (None, [], "Updated"),
        ([], None, "Updated"),
        ("invalid", [], "Updated"),
        ([], ["invalid"], "Updated"),
        ([], [{"type": "emoji", "emoji": " \n"}], "Updated"),
        ([], [{"type": "custom_emoji", "custom_emoji_id": 1234}], "Updated"),
        ([], [{"type": "future_reaction", "detail": "unknown"}], "Updated"),
    ],
)
def test_reaction_changes_preserve_original(old: JsonValue, new: JsonValue, expected: str) -> None:
    event: dict[str, JsonValue] = {
        "message_id": 7824,
        "old_reaction": old,
        "new_reaction": new,
    }
    original = deepcopy(event)
    assert format_preview(event) == "Reaction #7824 | " + expected
    assert event == original


@pytest.mark.parametrize("message_id", [None, True, -1, "7824"])
def test_reaction_invalid_message_id_is_not_displayed(message_id: JsonValue) -> None:
    assert format_preview({"message_id": message_id, "old_reaction": [], "new_reaction": []}) == (
        "Reaction | Unchanged"
    )


def test_partial_reaction_and_long_labels_are_safe_and_bounded() -> None:
    assert format_preview({"old_reaction": []}) == "Reaction | Updated"
    assert format_preview({"new_reaction": []}) == "Reaction | Updated"
    event: dict[str, JsonValue] = {
        "message_id": 7824,
        "old_reaction": [],
        "new_reaction": [{"type": "custom_emoji", "custom_emoji_id": "1234\n" * 100}],
    }
    original = deepcopy(event)
    preview = format_preview(event)
    assert len(preview) == 160
    assert preview.startswith("Reaction #7824 | Set Custom emoji 1234 1234")
    assert preview.endswith("...")
    assert event == original


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"emoji": "\U0001f3e6"}, "\U0001f3e6 (custom)"),
        ({"emoji": "\U0001f44b", "set_name": "ExamplePack"}, "\U0001f44b (custom, ExamplePack)"),
        ({"set_name": "ExamplePack"}, "Custom emoji 123 (ExamplePack)"),
        ({}, "Custom emoji 123"),
        ({"emoji": None, "set_name": 7}, "Custom emoji 123"),
        ({"emoji": "\U0001f44b", "set_name": "Pack\nName"}, "\U0001f44b (custom, Pack Name)"),
    ],
)
@pytest.mark.parametrize("clear", [False, True])
def test_custom_reaction_preview_uses_available_metadata(
    fields: dict[str, JsonValue], expected: str, clear: bool
) -> None:
    reaction: JsonValue = {"type": "custom_emoji", "custom_emoji_id": "123"}
    event: dict[str, JsonValue] = {
        "message_id": 42,
        "old_reaction": [reaction] if clear else [],
        "new_reaction": [] if clear else [reaction],
    }
    metadata: dict[str, JsonValue] = {
        "status": "ok",
        "stickers": [{"custom_emoji_id": "123", **fields}],
    }
    before = deepcopy((event, metadata))
    assert (
        format_preview(event, metadata)
        == f"Reaction #42 | {'Clear' if clear else 'Set'} {expected}"
    )
    assert (event, metadata) == before


def test_distinct_custom_ids_with_same_base_are_a_replacement() -> None:
    event: dict[str, JsonValue] = {
        "old_reaction": [{"type": "custom_emoji", "custom_emoji_id": "1"}],
        "new_reaction": [{"type": "custom_emoji", "custom_emoji_id": "2"}],
    }
    metadata: dict[str, JsonValue] = {
        "stickers": [
            {"custom_emoji_id": "1", "emoji": "\U0001f44d", "set_name": "First"},
            {"custom_emoji_id": "2", "emoji": "\U0001f44d", "set_name": "Second"},
        ]
    }
    assert format_preview(event, metadata) == (
        "Reaction | Set \U0001f44d (custom, Second) | Clear \U0001f44d (custom, First)"
    )


@pytest.mark.parametrize("stickers", [None, "invalid", [None, {}, {"custom_emoji_id": 1}]])
def test_malformed_metadata_falls_back_to_id(stickers: JsonValue) -> None:
    event: dict[str, JsonValue] = {
        "old_reaction": [],
        "new_reaction": [{"type": "custom_emoji", "custom_emoji_id": "123"}],
    }
    assert format_preview(event, {"stickers": stickers}) == "Reaction | Set Custom emoji 123"


def test_long_custom_set_label_is_bounded() -> None:
    event: dict[str, JsonValue] = {
        "old_reaction": [],
        "new_reaction": [{"type": "custom_emoji", "custom_emoji_id": "123"}],
    }
    metadata: dict[str, JsonValue] = {
        "stickers": [{"custom_emoji_id": "123", "emoji": "\U0001f44b", "set_name": "a" * 300}]
    }
    result = format_preview(event, metadata)
    assert len(result) == 160 and result.endswith("...")
