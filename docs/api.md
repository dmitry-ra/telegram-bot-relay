# HTTP and MCP

Both interfaces require the bearer secret from api_token_file and bind to
loopback. Validate Host and Origin; do not expose directly to the internet.
Maximum request body is 1 MiB. All outgoing destinations are checked against
outgoing_chats, independently of inbound sender permissions.

POST /v1/operations accepts one discriminated operation object. Unknown fields,
wrong types, ambiguous file sources and invalid albums are rejected. GET
/openapi.json returns the versioned OpenAPI3.1 contract; GET /healthz reports
HTTP availability and journal position (not Telegram reachability).

```json
{"method":"send_message","chat_id":123456789,"text":"Hello","reply_to":42}
```

HTTP 200 carries an operation outcome: sent, denied, failed or uncertain. Inspect
status, result and error; do not equate HTTP 200 with Telegram delivery.
HTTP 400 is schema failure, 401 authentication, 403 origin/host, 413 body limit.

Connect an MCP client to http://127.0.0.1:10881/mcp with the same Authorization
header. No subprocess or separate poller is launched. Tools accept a typed
request object and return the same Outcome model:

| Tool | Operation |
| --- | --- |
| send_message | Text, optional HTML/MarkdownV2, optional reply/thread |
| reply | Text with required reply_to |
| send_media | Photo, document, voice, audio, video, round video, animation, sticker, album |
| transfer_message | forward_message or copy_message between allowed chats |
| edit_message | Edit text and formatting |
| set_reaction | Set one emoji reaction |
| answer_callback | Answer a button callback |

Read-only custom emoji inspection is available through MCP `inspect_custom_emoji`
and POST `/v1/custom-emoji`, sharing the same implementation and authentication:

```json
{"custom_emoji_ids":["123456789"],"download_thumbnail":true}
```

The result reports `ok` or `failed`, sticker metadata and `missing_ids`.
Optional thumbnail downloads honor the attachment download policy and report
their status and local archive path. A thumbnail failure preserves available
metadata. Inspection requests/results are journaled separately from outgoing
operations; inspection never sends a message or changes a reaction.

Files use exactly one local path or Telegram file_id. Albums contain 2-10
photos/videos, only documents, or only audio. Document mode preserves bytes;
photo mode delegates Telegram's normal image processing. Transfer operations
require both source and destination chats to be allowed.

The SDK supports more Bot API methods than the relay exposes as tools. All received
update types are preserved, including forwards, entities, captions, album IDs,
edits, polls, callbacks, service events and unknown fields. No automatic content
interpretation, transcription, moderation, generic Bot API execution or webhook
ingress is implemented. See docs/operations.md for execution and testing.

Polling explicitly subscribes to the update categories known to the pinned SDK,
including opt-in reaction categories. New Telegram update categories may require
an SDK update before subscription. Every received update, including unknown
fields and envelopes, is retained as raw JSON. Omitting `allowed_updates` would
reuse the bot's previous subscription, while an empty list excludes reactions;
neither expresses an all-future-categories subscription with reactions enabled.
