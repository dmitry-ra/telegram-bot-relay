"""Display-only Telegram summaries; original updates remain untouched."""

from pydantic import JsonValue

MEDIA = {
    "photo": "\U0001f5bc\ufe0f Photo",
    "voice": "\U0001f3a4 Voice",
    "audio": "\U0001f3b5 Audio",
    "video": "\U0001f3ac Video",
    "animation": "\U0001f39e\ufe0f Animation",
    "video_note": "\U0001f4f9 Video note",
    "sticker": "\U0001f3f7\ufe0f Sticker",
    "document": "\U0001f4ce Document",
    "contact": "\U0001f464 Contact",
    "venue": "\U0001f4cd Venue",
    "location": "\U0001f4cd Location",
    "poll": "\U0001f4ca Poll",
    "dice": "\U0001f3b2 Dice",
}


def clean(value: JsonValue) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())


def reply_context(event: dict[str, JsonValue]) -> str:
    parent = event.get("reply_to_message")
    if not isinstance(parent, dict):
        parent = event.get("external_reply")
    quote = event.get("quote")
    if not isinstance(parent, dict) and not isinstance(quote, dict):
        return ""
    parent = parent if isinstance(parent, dict) else {}
    label = "\u21a9"
    message_id = parent.get("message_id")
    if type(message_id) is int and message_id >= 0:
        label += f" #{message_id}"
    excerpt = clean(quote.get("text")) if isinstance(quote, dict) else ""
    excerpt = excerpt or clean(parent.get("text")) or clean(parent.get("caption"))
    if not excerpt:
        excerpt = next((title for kind, title in MEDIA.items() if kind in parent), "")
    if excerpt:
        excerpt = excerpt if len(excerpt) <= 20 else excerpt[:17] + "..."
        label += ' "' + excerpt + '"'
    return label


def format_preview(event: dict[str, JsonValue]) -> str:
    parts: list[str] = []
    reply = reply_context(event)
    if reply:
        parts.append(reply)
    if event.get("forward_origin"):
        parts.append("\u21aa\ufe0f Forward")
    for kind, label in MEDIA.items():
        if kind not in event:
            continue
        parts.append(label)
        media = event[kind]
        if isinstance(media, dict):
            name = clean(media.get("file_name"))
            if name:
                parts.append(name)
            duration = media.get("duration")
            if type(duration) is int and duration >= 0:
                parts.append(f"{duration}s")
            for key in ("title", "question", "emoji"):
                detail = clean(media.get(key))
                if detail:
                    parts.append(detail)
        break
    text = clean(event.get("text")) or clean(event.get("caption"))
    if text:
        parts.append(text)
    elif not parts:
        parts.append(
            "\U0001f518 Button interaction"
            if "data" in event or "game_short_name" in event
            else "\U0001f4e8 Telegram event"
        )
    preview = " | ".join(parts)
    return preview if len(preview) <= 160 else preview[:157] + "..."
