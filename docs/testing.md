# Test strategy and acceptance

Normal tests never contact Telegram. Use uv run --locked python tools/check.py
before a code commit; CI repeats it on Python3.12,3.13,3.14. Black, Ruff,
strict mypy, compileall, schema drift and ASCII checks run before pytest.
Branch coverage must be at least 90%; coverage alone is not acceptance.

| Boundary | Verification |
| --- | --- |
| Access | Numeric identity, deny-all default, public opt-in, group/anonymous cases, outgoing source/destination, callback provenance |
| Input | Original Unicode/unknown fields, all decisions archived, offset unchanged on disk failure, redelivery after restart |
| Journal | Real fsync path, writer exclusion, strict envelope, sequence integrity, incomplete tail versus complete corruption |
| Rotation | Cron boundary, continuation/backlink, interruption before/after link publication, late attachments in original period |
| Attachments | Download policy, denied-file option, generated paths, byte/time limits, failed downloads, outgoing snapshots |
| Operations | Shared HTTP/MCP behavior, structured results/errors, no send without durable request, uncertain crash/network outcome |
| Network | Real local HTTP/MCP server and client, bearer/Host/Origin checks, strict payloads/body limit, fake Bot API methods/files |
| Gateway | Disabled mode, outage and catch-up, malformed acceptance, denied-input filtering, explicit latest start, restart progress |
| Lifecycle | Worker cancellation, retry/backoff, conflict shutdown, archive identity, actual HTTP server shutdown |
| Packaging | Wheel and sdist installed outside checkout, CLI version, packaged schemas and skill |

tools/mutation_smoke.py runs isolated source copies with three targeted
mutations: bypass outgoing access, advance Telegram offset before commit,
and remove journal fsync. Each has a passing baseline and must fail its
corresponding safety test. It is a focused development check, not a required
large mutation campaign on every CI job.

Parallel local matrix jobs must use distinct UV_PROJECT_ENVIRONMENT paths and
COVERAGE_FILE values. Reusing one .venv for different interpreters concurrently
invalidates the run. The default .venv remains the pinned development version.

Live acceptance is explicit and outside CI. After checking the bot has no
other receiver, run tools/live_trial.py with private config, an allowed chat,
and a private evidence directory (0700). Existing public evidence paths are
rejected, and generated artifacts use 0600 permissions. It sends text through REST, then text,
document and image through real Streamable HTTP MCP. An optional incoming
RELAY_INBOUND_CHECK message verifies polling and archival; use a minute cron
schedule to exercise real directory rotation. Evidence is report.json and
outcomes.json plus the archive; the tool closes its temporary service.

For full model acceptance, connect a separate test receiver to the same /mcp,
install the source skill, and route saved authorized control input through
Gateway. Verify a registered MCP reply tool invocation, Telegram's returned
message ID and correlated journal completion. A terminal-only answer does not
pass. Keep unrelated archived messages out of the reply test. Real media
reception/download and every Bot API feature require their own live cases;
local fixtures do not establish real-network support for untested features.
