# Run Telegram Bot Relay

For a complete terminal-and-Telegram receiver setup with your own bot, see
[Connect a bot to Codex](codex.md). It includes both delivery directions,
credentials, skills, conversation binding and a second-bot checklist.

## Configure

Install from a source checkout or a locally built package. The project is
pre-release; this guide does not assume a published PyPI release. The native
Codex integration uses an experimental compatibility interface.

Install pinned uv 0.13.6; uv sync --locked --group dev creates the pinned
development Python3.14.10 environment. Python3.12-3.14 is tested by CI.
Copy examples/config.toml to a private directory, set real paths and numeric
allowlists, and create separate token files with permissions 0600. Token files
contain only the value, not an environment assignment. Existing archive roots and
segment directories must be private (0700); event files and the writer lock must
be private (0600). Startup rejects group/other permissions before reading records;
it does not silently change existing archive permissions.

## Run

Start with uv run --locked telegram-bot-relay --config /absolute/config.toml.
For installed packages use the same command without uv run. Optional user
systemd template is in systemd/. One token has one polling owner: stop a previous
consumer before starting; local duplicate bot IDs/data roots are locked, and a
remote poller conflict stops the daemon without taking over its webhook.

## Optional Gateway delivery

No Gateway configuration is needed to archive input or send through HTTP/MCP.
If enabled, create the Gateway target separately and choose start=oldest to
forward the archive or latest to process future messages. Changing destinations
starts a distinct delivery history. Do not edit the archive or delete old
periods while attachments or deliveries may reference them. No automatic
purge, distributed locking or retention coordination with readers is provided.

Install skills/telegram-message into the receiver's skill directory. When
using Gateway also install its generic packet skill. The Telegram skill owns
payload interpretation; Gateway's core and skill remain source-independent.
The installed wheel includes the same skill under
telegram_bot_relay/skills/telegram-message/SKILL.md.

## Incoming events and previews

Gateway previews retain plain message text. Media previews include a type icon
and label, followed by available file name, duration and caption. Other supported
types include contacts, locations, venues, polls and dice; forwards have a marker
and button callbacks have an interaction label. Unknown types keep a generic
event label. Previews normalize whitespace and are capped at 160 characters;
Gateway may shorten the visible line further. Full original updates and download
metadata are preserved separately. Voice previews do not require transcription.

Replies begin with a reply arrow and the parent's message ID when provided.
A selected quote is preferred; otherwise a short parent text, caption or media
label supplies context. This excerpt is capped at 20 characters so reply and
forward markers and media labels remain near the start of the preview. Reply
does not imply forwarding, and both markers can occur together. Missing context
does not invent an author or a parent ID. Full reply/quote JSON remains available
separately, including quote position and manual-selection metadata.

Reaction previews show Set/Clear changes and the target message ID. Custom
reactions use the associated emoji and set name when available, for example
`Set <emoji> (custom, ExamplePack)`; missing metadata falls back to the custom ID.
Metadata lookup is separate from formatting, bounded to three seconds and saved
with the prepared event for retries/recovery. Full `reaction_metadata` accompanies
the original update. Automatic enrichment does not download thumbnails. See
[Gateway delivery limits](architecture.md#gateway-delivery-and-limits) for large
metadata, and [custom emoji inspection](api.md#custom-emoji-inspection) for an
explicit lookup or thumbnail download.

## Update an instance

After an editable source update, restart only the Relay daemon to load new code.
An existing Codex conversation and Gateway target can remain running. The MCP
URL and credentials stay the same; verify a tool call after restart and reconnect
the MCP client if it reports a stale session. Previously displayed lines are not
rewritten, and updating an installed skill may require the receiver to reread it.

## Verify an instance

Tests and checks:

```sh
uv run --locked python tools/check.py
uv build
uv run --locked python tools/package_smoke.py
```

Tests use real journals, local fake Bot API endpoints, real HTTP and a real
Streamable HTTP MCP client. They cover access denial, raw field preservation,
files, retries, crash boundaries, rotation, restart and optional Gateway.
Live tests require explicit selection and local credentials; CI has no bot
token and never sends Telegram messages. The reproducible live tool reports
exact steps and saves evidence in the chosen private directory.

## Stop and preserve archives

To stop, interrupt the foreground daemon or stop its systemd instance. No
external service is installed automatically. Closing a client does not stop
the daemon. Remove only test instances you created; preserve the archive for
review. Uninstall using uv tool uninstall telegram-bot-relay if installed by uv.

## Access-denied notifications

Access-denied notifications are sent for every distinct denied update to every
notify_chats recipient, through the same audited outgoing operations as MCP and
HTTP. No time window suppresses events. The legacy notify_interval configuration
key is accepted but ignored, so existing deployments need no private-config edit.
Each plain-text part begins with U+26A0 U+FE0F Access Denied and has a part number.
Generated labels are English; this warning icon is the only generated emoji.
Original names, text and emoji are preserved in their original language. The
notice includes available sender name/username/ID, bot ID, receipt timestamp,
reason, journal sequence and the entire original update as formatted JSON. Long
updates are split at Unicode character boundaries within Telegram's text limit;
no fields are truncated. Missing usernames are explicitly identified. Names and
usernames are event-time metadata, never authorization identities. Transport
outcomes remain sent/failed/uncertain; uncertain sends are not automatically
repeated. This change adds no separate delivery queue or profile lookup.

## Outgoing files

Outgoing media snapshots keep the original file basename inside a unique
operation directory. Telegram receives the readable original filename, while
separate operations and album items cannot overwrite one another.

## Configuration and archive boundaries

Gateway URLs must be HTTP(S) base URLs without userinfo, query or fragment
(including empty delimiters). Use gateway.token_file for authentication. Invalid
URLs are rejected during configuration loading before journal or network use.

Archive and evidence roots must have a stable namespace controlled by the owner
account or administrator, including their parent directories. Permission checks
do not isolate against actors able to replace these roots or modify the process
and its files. Derived archive directories reject symlinks and persist their
parent directory entries before results can refer to newly created files.
