"""REST and Streamable HTTP MCP in one application and lifecycle."""

import hmac
from typing import Any
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from telegram_bot_relay.application.custom_emoji import (
    InspectCustomEmoji,
    Inspection,
)
from telegram_bot_relay.application.custom_emoji import (
    inspect_custom_emoji as inspect_emoji,
)
from telegram_bot_relay.application.models import (
    Callback,
    EditMessage,
    Outcome,
    Reaction,
    SendMedia,
    SendMessage,
    Transfer,
    operation_adapter,
)
from telegram_bot_relay.application.operations import Operations


class Security:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app, self.token = app, token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        expected = ("Bearer " + self.token).encode()
        if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
            await JSONResponse({"error": "unauthorized"}, 401)(scope, receive, send)
            return
        host = headers.get(b"host", b"").decode("latin1")
        origin = headers.get(b"origin")
        try:
            address = urlsplit("http://" + host)
            hostname = address.hostname
            _ = address.port
            if address.netloc != host or address.username is not None:
                hostname = None
        except ValueError:
            hostname = None
        if hostname not in ("localhost", "127.0.0.1") or (
            origin is not None
            and origin.decode("latin1") not in ("http://" + host, "https://" + host)
        ):
            await JSONResponse({"error": "invalid_origin"}, 403)(scope, receive, send)
            return
        size = 0
        chunks = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > 1_048_576:
                await JSONResponse({"error": "request_too_large"}, 413)(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        consumed = False

        async def buffered() -> Message:
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, buffered, send)


def create_app(operations: Operations, token: str) -> ASGIApp:
    mcp: MCPServer[Any] = MCPServer("telegram-bot-relay")

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
    async def inspect_custom_emoji(request: InspectCustomEmoji) -> Inspection:
        """Read custom emoji metadata and optionally download thumbnails; sends no messages."""
        return await inspect_emoji(
            request, operations.config, operations.journal, operations.client, "mcp"
        )

    @mcp.tool()
    async def send_message(request: SendMessage) -> Outcome:
        """Send text to an allowed Telegram destination, optionally replying."""
        return await operations.execute(request, "mcp")

    @mcp.tool()
    async def reply(request: SendMessage) -> Outcome:
        """Reply to an incoming message using its chat_id and reply_to."""
        if request.reply_to is None:
            raise ValueError("reply_to is required")
        return await operations.execute(request, "mcp")

    @mcp.tool()
    async def send_media(request: SendMedia) -> Outcome:
        """Send a file, media or album, preserving explicit photo/document mode."""
        return await operations.execute(request, "mcp")

    @mcp.tool()
    async def transfer_message(request: Transfer) -> Outcome:
        """Forward or copy a message between allowed chats."""
        return await operations.execute(request, "mcp")

    @mcp.tool()
    async def edit_message(request: EditMessage) -> Outcome:
        """Edit the text of a Telegram message."""
        return await operations.execute(request, "mcp")

    @mcp.tool()
    async def set_reaction(request: Reaction) -> Outcome:
        """Set a Telegram message reaction."""
        return await operations.execute(request, "mcp")

    @mcp.tool()
    async def answer_callback(request: Callback) -> Outcome:
        """Answer an authorized callback query."""
        return await operations.execute(request, "mcp")

    async def execute(request: Request) -> JSONResponse:
        try:
            operation = operation_adapter.validate_json(await request.body())
        except ValidationError:
            return JSONResponse({"error": "invalid_operation"}, 400)
        result = await operations.execute(operation, "http")
        return JSONResponse(result.model_dump(mode="json"))

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ready", "journal_seq": operations.journal.seq})

    async def inspect(request: Request) -> JSONResponse:
        try:
            query = InspectCustomEmoji.model_validate_json(await request.body())
        except ValidationError:
            return JSONResponse({"error": "invalid_inspection"}, 400)
        result = await inspect_emoji(
            query, operations.config, operations.journal, operations.client, "http"
        )
        return JSONResponse(result.model_dump(mode="json"))

    async def specification(request: Request) -> JSONResponse:
        from telegram_bot_relay.interfaces.specification import openapi

        return JSONResponse(openapi())

    app: Starlette = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )
    app.router.routes.extend(
        [
            Route("/v1/operations", execute, methods=["POST"]),
            Route("/v1/custom-emoji", inspect, methods=["POST"]),
            Route("/healthz", health),
            Route("/openapi.json", specification),
        ]
    )
    return Security(app, token)
