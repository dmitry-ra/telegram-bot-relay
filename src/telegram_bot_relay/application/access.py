"""Authorization is independent of archival and message interpretation."""

from pydantic import JsonValue

from telegram_bot_relay.config import Access, StrictModel


class Decision(StrictModel):
    allowed: bool
    reason: str | None = None


def authorize(update: dict[str, JsonValue], policy: Access) -> Decision:
    event = next((v for k, v in update.items() if k != "update_id" and isinstance(v, dict)), {})
    if not isinstance(event, dict):
        return Decision(allowed=False, reason="missing_event")
    sender = event.get("from") or event.get("user")
    chat = event.get("chat")
    nested = event.get("message")
    if not isinstance(chat, dict) and isinstance(nested, dict):
        chat = nested.get("chat")
    if not isinstance(sender, dict) or type(sender.get("id")) is not int:
        return Decision(allowed=False, reason="missing_sender")
    if event.get("sender_chat"):
        return Decision(allowed=False, reason="anonymous_sender")
    if not policy.public and sender["id"] not in policy.allowed_users:
        return Decision(allowed=False, reason="user_not_allowed")
    if isinstance(chat, dict):
        if chat.get("type") != "private" and not policy.groups:
            return Decision(allowed=False, reason="chat_type_not_allowed")
        if policy.allowed_chats and chat.get("id") not in policy.allowed_chats:
            return Decision(allowed=False, reason="chat_not_allowed")
        if chat.get("type") != "private" and chat.get("id") not in policy.allowed_chats:
            return Decision(allowed=False, reason="group_not_allowed")
    return Decision(allowed=True)
