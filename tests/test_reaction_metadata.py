"""Optional reaction enrichment must preserve raw events and tolerate lookup failure."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetCustomEmojiStickers
from pydantic import JsonValue

from telegram_bot_relay.application.custom_emoji import prepare_reaction_metadata
from tests.helpers import FakeClient


def reaction_update(old: list[JsonValue], new: list[JsonValue]) -> dict[str, JsonValue]:
    return {
        "update_id": 1,
        "message_reaction": {
            "message_id": 42,
            "old_reaction": old,
            "new_reaction": new,
        },
    }


def custom(identity: JsonValue) -> dict[str, JsonValue]:
    return {"type": "custom_emoji", "custom_emoji_id": identity}


def test_metadata_enriches_both_set_and_clear_preserving_complete_original() -> None:
    async def run() -> None:
        client = FakeClient()
        returned: list[JsonValue] = [
            {"custom_emoji_id": "2", "emoji": "\U0001f44b", "set_name": "Example_set"},
            {
                "custom_emoji_id": "1",
                "emoji": "\U0001f3e6",
                "width": 100,
                "is_video": True,
                "thumbnail": {"file_id": "thumb", "width": 100},
                "future_field": {"preserved": True},
            },
            {"custom_emoji_id": "999", "emoji": "unrequested"},
            None,
        ]
        lookup = AsyncMock(return_value=returned)
        original = reaction_update([custom("1")], [custom("2"), custom("1"), custom("3")])
        before = deepcopy(original)
        with patch.object(client, "custom_emoji", lookup):
            metadata = await prepare_reaction_metadata(original, client, True)
        lookup.assert_awaited_once_with(["1", "2", "3"])
        assert metadata == {
            "status": "ok",
            "stickers": [returned[1], returned[0]],
            "missing_ids": ["3"],
        }
        assert original == before
        assert not client.sent and not client.downloaded

    asyncio.run(run())


@pytest.mark.parametrize("clear", [False, True])
def test_metadata_lookup_handles_set_and_clear_without_other_side_effects(clear: bool) -> None:
    async def run() -> None:
        client = FakeClient()
        lookup = AsyncMock(return_value=[])
        original = reaction_update([custom("1")] if clear else [], [] if clear else [custom("1")])
        with patch.object(client, "custom_emoji", lookup):
            result = await prepare_reaction_metadata(original, client, True)
        lookup.assert_awaited_once_with(["1"])
        assert result == {"status": "ok", "stickers": [], "missing_ids": ["1"]}

    asyncio.run(run())


@pytest.mark.parametrize(
    ("original", "allowed"),
    [
        (reaction_update([], [custom("1")]), False),
        ({"message": {"old_reaction": [], "new_reaction": [custom("1")]}}, True),
        ({"message_reaction": None}, True),
        (reaction_update([], [{"type": "emoji", "emoji": "\U0001f44d"}]), True),
        (reaction_update([], [{"type": "paid"}]), True),
        ({"message_reaction": {"old_reaction": None, "new_reaction": "invalid"}}, True),
    ],
)
def test_unneeded_or_denied_metadata_does_not_call_telegram(
    original: dict[str, JsonValue], allowed: bool
) -> None:
    async def run() -> None:
        client = FakeClient()
        lookup = AsyncMock()
        before = deepcopy(original)
        with patch.object(client, "custom_emoji", lookup):
            assert await prepare_reaction_metadata(original, client, allowed) == {}
        lookup.assert_not_awaited()
        assert original == before

    asyncio.run(run())


@pytest.mark.parametrize("identity", [None, 1, [], "", "x", "\u0661", "1" * 65])
def test_invalid_custom_identity_safely_falls_back_without_network(identity: JsonValue) -> None:
    async def run() -> None:
        client = FakeClient()
        lookup = AsyncMock()
        original = reaction_update([], [custom(identity)])
        with patch.object(client, "custom_emoji", lookup):
            result = await prepare_reaction_metadata(original, client, True)
        assert result == {
            "status": "failed",
            "stickers": [],
            "missing_ids": [],
            "error": "ValidationError",
        }
        lookup.assert_not_awaited()

    asyncio.run(run())


def test_excessive_unique_custom_ids_fall_back_without_network() -> None:
    async def run() -> None:
        client = FakeClient()
        lookup = AsyncMock()
        original = reaction_update([], [custom(str(i)) for i in range(201)])
        with patch.object(client, "custom_emoji", lookup):
            result = await prepare_reaction_metadata(original, client, True)
        assert result["status"] == "failed" and result["error"] == "ValidationError"
        lookup.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        OSError("private detail"),
        TelegramBadRequest(
            method=GetCustomEmojiStickers(custom_emoji_ids=["1"]), message="private detail"
        ),
        ValueError("private detail"),
    ],
)
def test_lookup_failure_is_metadata_only_and_does_not_expose_error_prose(error: Exception) -> None:
    async def run() -> None:
        client = FakeClient()
        original = reaction_update([], [custom("1")])
        before = deepcopy(original)
        with patch.object(client, "custom_emoji", AsyncMock(side_effect=error)):
            result = await prepare_reaction_metadata(original, client, True)
        assert result == {
            "status": "failed",
            "stickers": [],
            "missing_ids": ["1"],
            "error": type(error).__name__,
        }
        assert "private detail" not in str(result)
        assert original == before

    asyncio.run(run())


def test_lookup_timeout_falls_back_and_cancels_optional_lookup() -> None:
    async def run() -> None:
        client = FakeClient()
        cancelled = False

        async def slow(ids: list[str]) -> list[JsonValue]:
            nonlocal cancelled
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled = True
                raise
            return []

        with (
            patch.object(client, "custom_emoji", slow),
            patch("telegram_bot_relay.application.custom_emoji.REACTION_METADATA_TIMEOUT", 0.001),
        ):
            result = await prepare_reaction_metadata(
                reaction_update([], [custom("1")]), client, True
            )
        assert result["status"] == "failed" and result["error"] == "TimeoutError"
        assert cancelled

    asyncio.run(run())


def test_service_cancellation_is_not_hidden_as_optional_metadata_failure() -> None:
    async def run() -> None:
        client = FakeClient()
        with patch.object(client, "custom_emoji", AsyncMock(side_effect=asyncio.CancelledError)):
            with pytest.raises(asyncio.CancelledError):
                await prepare_reaction_metadata(reaction_update([], [custom("1")]), client, True)

    asyncio.run(run())
