"""Versioned append-only journal envelope."""

from typing import Literal

from pydantic import Field, JsonValue

from telegram_bot_relay.config import StrictModel


class Record(StrictModel):
    schema_version: Literal[1] = 1
    seq: int = Field(ge=1)
    timestamp: str
    period: str
    type: Literal[
        "segment_started",
        "bot_started",
        "rotate",
        "tail_recovered",
        "message_received",
        "message_prepared",
        "gateway_started",
        "gateway_accepted",
        "operation_requested",
        "operation_artifacts",
        "operation_completed",
        "inspection_requested",
        "inspection_completed",
    ]
    data: dict[str, JsonValue]
