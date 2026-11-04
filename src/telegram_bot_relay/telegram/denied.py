"""Complete plain-text denial notices, without filtering or truncation."""

import json

from pydantic import JsonValue

from telegram_bot_relay.telegram.events import event_and_actor


def denial_notifications(
    original: dict[str, JsonValue],
    bot_id: int | None,
    timestamp: str,
    access: dict[str, JsonValue],
    seq: int,
) -> list[str]:
    _, sender = event_and_actor(original)
    name = " ".join(str(sender[k]) for k in ("first_name", "last_name") if sender.get(k))
    username = sender.get("username")
    reason = access.get("reason")
    text = (
        f"Bot: {bot_id}\n"
        f"Name: {json.dumps(name, ensure_ascii=False) if name else 'not provided'}\n"
        f"Username: {'@' + str(username) if username else 'not provided'}\n"
        f"User ID: {sender.get('id', 'not provided')}\n"
        f"Received: {timestamp}\n"
        f"Reason: {reason}\n"
        f"Journal record: {seq}\n\n"
        "Original event (JSON):\n" + json.dumps(original, ensure_ascii=False, indent=2)
    )
    # Count UTF-16 units conservatively; split only between Unicode characters.
    parts: list[str] = []
    start = units = 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > 3900:
            parts.append(text[start:index])
            start, units = index, 0
        units += width
    parts.append(text[start:])
    return [
        f"\u26a0\ufe0f Access Denied [{index}/{len(parts)}]\n\n{part}"
        for index, part in enumerate(parts, 1)
    ]
