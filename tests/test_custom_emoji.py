"""Read-only emoji inspection without credentials or real Telegram calls."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Sticker
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from pydantic import JsonValue, ValidationError

from telegram_bot_relay.application.custom_emoji import InspectCustomEmoji, inspect_custom_emoji
from telegram_bot_relay.application.operations import Operations
from telegram_bot_relay.interfaces.server import create_app
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.telegram.client import Client
from tests.helpers import FakeClient, configuration
from tests.test_interfaces import running


class EmojiClient(FakeClient):
    async def custom_emoji(self, ids: list[str]) -> list[JsonValue]:
        if self.failure:
            raise self.failure
        return [
            {"custom_emoji_id": "2", "file_id": "two", "emoji": "\U0001f44d"},
            {"custom_emoji_id": "1", "file_id": "one", "thumbnail": {"file_id": "thumb"}},
            {"custom_emoji_id": "999", "file_id": "unrequested"},
        ]


@pytest.mark.parametrize("ids", [[], [""], ["-1"], ["x"], ["\u0661"], ["1" * 65], ["1"] * 201])
def test_invalid_ids(ids: list[str]) -> None:
    with pytest.raises(ValidationError):
        InspectCustomEmoji(custom_emoji_ids=ids)


def test_inspection_orders_filters_and_downloads(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = EmojiClient()
        result = await inspect_custom_emoji(
            InspectCustomEmoji(custom_emoji_ids=["1", "2", "1", "3"], download_thumbnail=True),
            config,
            journal,
            client,
            "test",
        )
        assert result.status == "ok"
        assert [s["custom_emoji_id"] for s in result.stickers] == ["1", "2"]
        assert result.missing_ids == ["3"]
        saved = result.stickers[0]["thumbnail_download"]
        assert isinstance(saved, dict) and saved["status"] == "saved"
        path = Path(str(saved["path"]))
        assert path.is_relative_to(journal.root)
        assert path.read_bytes() == b"example file"
        assert path.stat().st_mode & 0o777 == 0o600
        assert result.stickers[1]["thumbnail_download"] == {"status": "unavailable"}
        assert not client.sent
        assert [r.type for r in journal.records()][-2:] == [
            "inspection_requested",
            "inspection_completed",
        ]
        journal.close()

    asyncio.run(run())


def test_thumbnail_failure_keeps_metadata(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = EmojiClient()
        with patch.object(client, "download", new=AsyncMock(side_effect=OSError("private detail"))):
            result = await inspect_custom_emoji(
                InspectCustomEmoji(custom_emoji_ids=["1"], download_thumbnail=True),
                config,
                journal,
                client,
                "test",
            )
        assert result.status == "ok"
        assert result.stickers[0]["file_id"] == "one"
        assert result.stickers[0]["thumbnail_download"] == {"status": "failed", "error": "OSError"}
        assert "private detail" not in str(result)
        config.attachments.download = False
        result = await inspect_custom_emoji(
            InspectCustomEmoji(custom_emoji_ids=["1"], download_thumbnail=True),
            config,
            journal,
            client,
            "test",
        )
        assert result.stickers[0]["thumbnail_download"] == {"status": "disabled"}
        client.failure = OSError("private detail")
        result = await inspect_custom_emoji(
            InspectCustomEmoji(custom_emoji_ids=["1"]), config, journal, client, "test"
        )
        assert result.status == "failed" and result.error == "OSError"
        assert "private detail" not in str(result)
        journal.close()

    asyncio.run(run())


def test_client_serializes_full_sticker_metadata() -> None:
    async def run() -> None:
        client = Client("123:synthetic-token")
        sticker = Sticker(
            file_id="one",
            file_unique_id="unique",
            type="custom_emoji",
            width=100,
            height=100,
            is_animated=False,
            is_video=False,
            custom_emoji_id="1",
            emoji="\U0001f44d",
        )
        with patch.object(
            client.bot, "get_custom_emoji_stickers", new=AsyncMock(return_value=[sticker])
        ) as lookup:
            result = await client.custom_emoji(["1"])
        lookup.assert_awaited_once_with(custom_emoji_ids=["1"])
        assert result[0] == sticker.model_dump(mode="json", exclude_unset=True)
        await client.close()

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["mkdir", "cleanup"])
def test_thumbnail_filesystem_failure_keeps_metadata(tmp_path: Path, stage: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = EmojiClient()
        operation = "mkdir" if stage == "mkdir" else "unlink"
        with (
            patch.object(Path, operation, side_effect=PermissionError("private path")),
            patch.object(client, "download", new=AsyncMock(side_effect=OSError("download failed"))),
        ):
            result = await inspect_custom_emoji(
                InspectCustomEmoji(custom_emoji_ids=["1"], download_thumbnail=True),
                config,
                journal,
                client,
                "test",
            )
        assert result.status == "ok" and result.stickers[0]["file_id"] == "one"
        download = result.stickers[0]["thumbnail_download"]
        assert isinstance(download, dict) and download["status"] == "failed"
        assert download["error"] == ("PermissionError" if stage == "mkdir" else "OSError")
        assert "private path" not in str(result)
        journal.close()

    asyncio.run(run())


def test_http_and_mcp_inspection_share_authenticated_operation(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        journal = Journal(Path(config.data_dir))
        client = EmojiClient()
        async with running(create_app(Operations(config, journal, client), "secret")) as url:
            async with create_mcp_http_client() as http:
                assert (
                    await http.post(url + "/v1/custom-emoji", json={"custom_emoji_ids": ["1"]})
                ).status_code == 401
                headers = {"Authorization": "Bearer secret"}
                assert (
                    await http.post(
                        url + "/v1/custom-emoji", json={"custom_emoji_ids": []}, headers=headers
                    )
                ).status_code == 400
                response = await http.post(
                    url + "/v1/custom-emoji", json={"custom_emoji_ids": ["1"]}, headers=headers
                )
                assert response.status_code == 200 and response.json()["status"] == "ok"
            async with create_mcp_http_client(headers={"Authorization": "Bearer secret"}) as http:
                async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        tool = next(t for t in tools.tools if t.name == "inspect_custom_emoji")
                        assert tool.annotations is not None and not tool.annotations.read_only_hint
                        assert tool.annotations.destructive_hint is False
                        result = await session.call_tool(
                            "inspect_custom_emoji", {"request": {"custom_emoji_ids": ["2"]}}
                        )
                        assert not result.is_error
                        assert (
                            result.structured_content is not None
                            and result.structured_content["status"] == "ok"
                        )
        assert not client.sent
        sources = [r.data["source"] for r in journal.records() if r.type == "inspection_requested"]
        assert sources == ["http", "mcp"]
        journal.close()

    asyncio.run(run())
