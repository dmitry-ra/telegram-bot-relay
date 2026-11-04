"""Select Telegram update envelopes and their available actor without policy."""

from pydantic import JsonValue


def event_and_actor(
    update: dict[str, JsonValue],
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    event = next((v for k, v in update.items() if k != "update_id" and isinstance(v, dict)), {})
    actor = event.get("from") or event.get("user")
    return event, actor if isinstance(actor, dict) else {}
