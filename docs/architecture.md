# Architecture and boundaries

One process owns one bot and one append-only archive. REST and Streamable HTTP
MCP run in the same Starlette application with shared operations. No stdio
proxy, SQL database or dependency on Message Gateway is required.

## Input and access

aiogram supplies Bot API methods, transport, files and errors. RawUpdates uses
the SDK method mechanism but returns original JSON objects rather than typed
Update models: future fields and unknown event types survive archival.
The polling loop explicitly controls offset. Every update, including denied
input, is committed with flush/fsync before advancing the next request offset.
Recent update IDs suppress redelivery after a restart at that boundary.

Access uses numeric users and chat IDs, never display names. Empty private
allowlists deny all. Groups require explicit enablement and an allowed chat.
Denied messages are archived in full; downstream forwarding is prohibited.
Optional denied-file downloads archive bytes only, without interpretation.

## Optional Gateway delivery

Independent workers prepare attachments and forward prepared records to
Gateway. Gateway outages do not stop input archival. Its start position is
explicitly oldest or latest and persisted as gateway_started. gateway_accepted
means HTTP queue acceptance, not model reading or completion. Denied input
is never forwarded. Gateway's own queue remains in memory.

## Operations and files

REST/MCP requests are typed and share destination validation. Local outgoing
files must resolve inside configured upload roots, are copied into the archive
before sending, and have explicit media kind. Initial symlinks are resolved; the
canonical file must remain under a configured root. Descriptor-based copying
rejects symlink replacements in any canonical path component and enforces the
regular-file type and byte limit on the opened file. Incoming downloads have bounded
time and bytes, generated names and durable completion records. Existing download
directories must be private and must not be symlinks; destination files are
created exclusively with 0600 permissions. A file archive
does not establish that its contents are safe or understood.

Incoming attachment `type_hints` preserve bounded filename/MIME/media-kind and
successful getFile path declarations, naming source and conflicts. A small
allowlist chooses a prospective suffix; unknown formats remain `.bin`. These
are not content detection. Existing raw updates, download consumers and older
prepared records remain valid. See [incoming files](incoming-files.md).

Outbound requested/completed records share operation_id. Successful results
preserve Telegram response data. Errors log exception classes, never token URLs.
Network ambiguity is recorded as uncertain and is not automatically retried.
On restart, unfinished operations are marked uncertain instead of resent.
Rejected schemas/authentication and protocol initialization are technical
traffic, not accepted application operations, and do not enter this journal.

## Rotation and recovery

Cron rotation uses the configured timezone and creates directories only when
events occur. Each closed events.jsonl ends with a relative next_events link;
the next segment starts with a reverse link. A new directory is created before
publishing its link. Recovery completes an interrupted handoff and preserves
an incomplete active-file tail before removing it. Each recovery backup is keyed
by source offset and content hash, atomically replaced and directory-synced before
truncation, preserving distinct tails and making crash retries idempotent. Complete malformed lines,
sequence gaps, unsafe paths and broken closed-segment links cause startup failure.
Late attachment results go into the active journal, referencing the original
message period; the bytes remain in that period's attachments directory.

Records have schema_version, seq, timestamp, period, type and data. The envelope
is strict; original Telegram content and operation results intentionally retain
arbitrary JSON. See the packaged JSON Schema. Readers filter record types and
follow continuation links; they own their offsets. Startup scans committed
records; no checkpoint database or automatic retention is implemented.

## Gateway delivery and limits

Delivery cannot promise exactly once across independent systems. A crash after
Gateway accepts but before its local receipt may duplicate forwarding. A Gateway
restart loses its in-memory queue. Archived records support investigation and
explicit replay, not proof of model completion. Telegram Bot API permissions,
privacy and update retention apply regardless of SDK feature coverage.

Custom reaction metadata is optional display/context enrichment; the original
update remains intact. If inline metadata exceeds Gateway's 65536-byte content
limit, forwarding uses a reference to the existing prepared journal record.
If that reference cannot fit, optional metadata stays in the journal while the
original content is forwarded. Original content that already exceeds the limit
is not truncated. Receivers need access to referenced archive paths. This adds
no cache service or separate message store.

## Startup ownership

The daemon and live trial validate the Telegram identity, archive identity and
local bot lock before constructing or starting the HTTP server. Initialization
is idempotent; direct worker startup uses the same validation and lock lifetime.
