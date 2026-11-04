"""Optional enrichment cannot prevent otherwise valid Gateway delivery."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from aiohttp import ClientResponseError, web
from aiohttp.test_utils import TestServer
from pydantic import JsonValue

from telegram_bot_relay.config import Gateway
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import Service
from tests.helpers import FakeClient, configuration


class MetadataClient(FakeClient):
    async def custom_emoji(self, identities: list[str]) -> list[JsonValue]:
        return [
            {
                "custom_emoji_id": identity,
                "emoji": "\U0001f44d",
                "set_name": "ExamplePack",
                "file_id": "x" * 1000,
            }
            for identity in identities
        ]


@pytest.mark.parametrize("mode", ["reference", "no_room", "raw_oversized"])
def test_enrichment_uses_durable_reference_without_truncating_original(
    tmp_path: Path, mode: str
) -> None:
    async def run() -> None:
        received: list[dict[str, JsonValue]] = []

        async def accept(request: web.Request) -> web.Response:
            body = await request.json()
            received.append(body)
            size = len(json.dumps(body["content"], ensure_ascii=False).encode("utf-8"))
            return web.json_response({"id": "accepted"}, status=202 if size <= 65536 else 400)

        app = web.Application()
        app.router.add_post("/v1/targets/work/messages", accept)
        async with TestServer(app) as server:
            config = configuration(tmp_path)
            config.gateway = Gateway(
                url=str(server.make_url("/")).rstrip("/"), target="work", start="oldest"
            )
            journal = Journal(Path(config.data_dir))
            service = Service(config, journal, MetadataClient())
            ids = [str(index) for index in range(200 if mode == "reference" else 1)]
            original: dict[str, JsonValue] = {
                "update_id": 1,
                "message_reaction": {
                    "message_id": 42,
                    "date": 1793487600,
                    "chat": {"id": 101, "type": "private"},
                    "user": {"id": 101, "username": "example"},
                    "old_reaction": [],
                    "new_reaction": [
                        {"type": "custom_emoji", "custom_emoji_id": value} for value in ids
                    ],
                },
                "padding": "",
            }
            base_size = len(json.dumps({**original, "downloads": []}, ensure_ascii=False).encode())
            if mode != "reference":
                original["padding"] = "a" * (65536 - base_size + (mode == "raw_oversized"))
            before = deepcopy(original)
            service.ingest(original)
            assert await service.process_once()
            if mode == "raw_oversized":
                with pytest.raises(ClientResponseError):
                    await service.forward_once()
            else:
                assert await service.forward_once()
            assert len(received) == 1
            content = received[0]["content"]
            assert isinstance(content, dict)
            assert {
                k: v for k, v in content.items() if k not in ("downloads", "reaction_metadata")
            } == before
            prepared = next(
                record for record in journal.records() if record.type == "message_prepared"
            )
            metadata = prepared.data["reaction_metadata"]
            assert isinstance(metadata, dict) and metadata["status"] == "ok"
            assert original == before
            if mode == "reference":
                reference = content["reaction_metadata"]
                assert isinstance(reference, dict) and reference["status"] == "referenced"
                records = [
                    json.loads(line)
                    for line in Path(str(reference["journal_path"])).read_text().splitlines()
                ]
                record = next(item for item in records if item["seq"] == reference["record_seq"])
                assert record["data"]["reaction_metadata"] == metadata
                assert (
                    len(
                        json.dumps(
                            {**before, "downloads": [], "reaction_metadata": metadata}
                        ).encode()
                    )
                    > 65536
                )
                assert "Set \U0001f44d (custom, ExamplePack)" in str(received[0]["preview"])
            elif mode == "no_room":
                assert "reaction_metadata" not in content
            else:
                assert content["reaction_metadata"] == metadata
            journal.close()

    asyncio.run(run())
