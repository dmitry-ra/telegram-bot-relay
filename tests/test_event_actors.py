"""Telegram actor identity stays consistent across policy, notices and forwarding."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import JsonValue

from telegram_bot_relay.application.access import authorize
from telegram_bot_relay.config import Access, Gateway
from telegram_bot_relay.journal.writer import Journal
from telegram_bot_relay.service import Service
from telegram_bot_relay.telegram.denied import denial_notifications
from telegram_bot_relay.telegram.events import event_and_actor
from tests.helpers import FakeClient, configuration


@pytest.mark.parametrize("kind", ["message", "message_reaction", "poll_answer", "callback_query"])
@pytest.mark.parametrize("display", ["username", "first_name", "id"])
def test_actor_consumers_preserve_identity_and_original(
    tmp_path: Path, kind: str, display: str
) -> None:
    async def run() -> None:
        bodies: list[dict[str, JsonValue]] = []

        async def receive(request: web.Request) -> web.Response:
            bodies.append(await request.json())
            return web.json_response({"id": "accepted"}, status=202)

        actor: dict[str, JsonValue] = {"id": 101}
        if display != "id":
            actor["first_name"] = "Example"
        if display == "username":
            actor["username"] = "example_user"
        chat: dict[str, JsonValue] = {"id": 101, "type": "private"}
        event: dict[str, JsonValue]
        if kind == "poll_answer":
            event = {"poll_id": "poll", "user": actor, "option_ids": [0]}
        elif kind == "message_reaction":
            event = {
                "message_id": 42,
                "chat": chat,
                "user": actor,
                "old_reaction": [],
                "new_reaction": [],
            }
        elif kind == "callback_query":
            event = {"id": "callback", "from": actor, "message": {"chat": chat}, "data": "x"}
        else:
            event = {"message_id": 42, "chat": chat, "from": actor, "text": "Hello"}
        original: dict[str, JsonValue] = {"update_id": 1, kind: event}
        before = deepcopy(original)
        assert event_and_actor(original) == (event, actor)
        assert authorize(original, Access(allowed_users=[101])).allowed
        denied = authorize(original, Access(allowed_users=[202]))
        assert denied.reason == "user_not_allowed"
        notice = "".join(
            denial_notifications(original, 123456, "timestamp", denied.model_dump(), 1)
        )
        assert "User ID: 101" in notice
        if display != "id":
            assert 'Name: "Example"' in notice
        if display == "username":
            assert "Username: @example_user" in notice
        if kind == "callback_query":
            assert (
                authorize(original, Access(allowed_users=[101], allowed_chats=[202])).reason
                == "chat_not_allowed"
            )

        app = web.Application()
        app.router.add_post("/v1/targets/work/messages", receive)
        async with TestServer(app) as server:
            config = configuration(tmp_path)
            config.gateway = Gateway(
                url=str(server.make_url("/")).rstrip("/"), target="work", start="oldest"
            )
            journal = Journal(Path(config.data_dir))
            try:
                service = Service(config, journal, FakeClient())
                service.ingest(original)
                source = next(r for r in journal.records() if r.type == "message_received")
                assert source.data["original"] == before
                assert source.data["access"] == {"allowed": True, "reason": None}
                assert await service.process_once()
                assert await service.forward_once()
                assert len(bodies) == 1
                assert bodies[0]["sender"] == str(
                    actor.get("username") or actor.get("first_name") or actor["id"]
                )
                assert bodies[0]["content"] == {**before, "downloads": []}
                assert original == before
            finally:
                journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("actor", [None, "invalid", {"id": True}])
def test_invalid_actor_is_denied(actor: JsonValue) -> None:
    original: dict[str, JsonValue] = {"message": {"from": actor}}
    assert authorize(original, Access(public=True)).reason == "missing_sender"


def test_actor_selection_retains_from_precedence_and_empty_fallback() -> None:
    user: dict[str, JsonValue] = {"id": 101}
    assert event_and_actor({"message": {"from": {}, "user": user}})[1] == user
    assert event_and_actor({"message": {"from": "invalid", "user": user}})[1] == {}
    assert event_and_actor({"update_id": 1}) == ({}, {})
    original: dict[str, JsonValue] = {"message": {"from": {"id": 202}, "user": user}}
    assert authorize(original, Access(allowed_users=[101])).reason == "user_not_allowed"
