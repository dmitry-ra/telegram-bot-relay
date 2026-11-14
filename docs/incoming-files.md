# Incoming file types and names

Relay preserves every raw Telegram update. Its recursive file discovery still
downloads every distinct `file_id`, subject to the existing access, byte and
time limits. This includes photos, documents, voice, audio, video, video notes,
animations, stickers and file descriptors in future message types. Unknown
types do not need a new download API method.

Prepared attachment records now add `type_hints`. The same optional object
appears in Gateway `downloads`. Existing `file_id`, `status`, `path` and `bytes`
fields retain their meanings; old journal entries without hints remain valid.
Previously prepared files are never renamed, moved or rewritten.

```json
{
  "file_id": "synthetic-voice-id",
  "status": "saved",
  "path": "attachments/0123456789abcdef0123456789abcdef.ogg",
  "bytes": 12345,
  "type_hints": {
    "media_kinds": ["voice"],
    "mime_type": "audio/ogg",
    "telegram_file_path": "voice/file_1.oga",
    "telegram_file_size": 12345,
    "suffix": ".ogg",
    "naming_source": "mime_type"
  }
}
```

Gateway expands a saved `path` to the actual local archive path as before.
`telegram_file_path` is separately reported by Telegram's `getFile`; it is not
the local archive path and must not be opened as one. No bot token or download
URL is stored in that field. Local Bot API absolute paths may appear as hints.
The actual saved byte count remains `bytes`, independently of a declared size.

## Small prospective naming policy

Local basenames are random identifiers. Relay examines only a final suffix,
normalizes it to lowercase and requires an explicit allowlist. It never uses
an original name or Telegram path as a local basename or directory. Names with
Unix or Windows directory components therefore cannot escape the archive.
Metadata copies are bounded; original claims remain in the raw update.

The first usable naming hint wins:

1. The original filename suffix, if allowlisted.
2. A known declared MIME type, using a small explicit mapping.
3. An allowlisted suffix from `getFile.file_path`, returned after a successful
   download. The same getFile request already used by the downloader supplies it.
4. An unambiguous animated/video sticker flag, choosing `.tgs` or `.webm`.
5. `.bin` for unknown or ambiguous formats.

The mapping covers common images including HEIC/HEIF, audio including Ogg and
MP3, video, documents and archives. It is not a general MIME registry. Generic
voice/audio/video/animation kinds alone do not prove a particular container;
static sticker format is likewise ambiguous without another hint. An unknown
suffix or `application/octet-stream` remains binary unless another useful hint
exists. A `.bin` filename does not override a known MIME declaration.

The source policy in `telegram/file_types.py` is authoritative. Telegram's
[media object fields](https://core.telegram.org/bots/api#available-types) and
[getFile contract](https://core.telegram.org/bots/api#getfile) describe the
available declarations; sender filenames and MIME types are optional.

## Conflicting declarations remain visible

A sender may provide `capture.JPG` and `image/heic`; Telegram may report a
different path suffix. Relay retains the bounded original name, declared MIME
and successful getFile path and marks the disagreement in `conflicts`. The
filename-first policy remains explicit instead of silently rewriting a claim.
Equivalent aliases such as `.jpeg`/`.jpg` or Ogg suffix variants do not conflict.

`media_kinds` records the enclosing Telegram media fields. When Telegram exposes
one file ID as both animation and document, there is one download with both
claims; the legacy descriptor selection still retains its last descriptor.
Raw updates, parent replies, quotes, forwards, captions, album IDs, topic IDs
and unknown fields remain unchanged. Album grouping behavior is not modified.

All hints are declarations, not byte-format detection, validation or a safety
assessment. Relay neither decodes media nor automatically transcribes, executes
or interprets a file. A misleading filename/MIME/path can describe arbitrary
bytes. Consumers that need the actual format must inspect the saved file with
an appropriate reader and retain their own result separately.

Skipped, size-limited and failed entries also retain available message hints;
they do not identify a readable downloaded file. Failures expose an error class,
not raw transport errors or token-bearing URLs. Download consumers that return
no metadata continue to work; their naming uses message hints or `.bin`.

## Verification and rollout

Normal tests use synthetic Telegram traffic. They cover all eight media kinds,
unknown types, contradictory/missing metadata, safe paths, getFile propagation,
limits, failures and recovery without replaying prepared input. The real local
HTTP fake Bot API still exercises the actual SDK download and bounded writer.
They do not establish untested live Telegram formats or sender acceptance.

Deploy only through the existing single poller for the requested bot. Prepare
an isolated reviewed checkout/environment, preserve the exact original service
unit and runtime binding, then stop the existing service and confirm its polling
process has exited before starting the replacement. Reuse the same private
configuration and archive; never reset offsets, rewrite JSONL or resend
unfinished outgoing operations. Startup recovery retains the existing uncertain
send behavior. A rollback restores the preserved service binding and starts it
only after the replacement has stopped. No archive format migration is needed.
