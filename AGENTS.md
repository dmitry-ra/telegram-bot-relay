# Telegram Bot Relay

One process owns one Telegram bot, archives inbound updates and files, and
serves shared HTTP/MCP outgoing operations. Message Gateway is optional.

## Use

- Operator tasks: [.agents/skills/operate-telegram-relay/SKILL.md](.agents/skills/operate-telegram-relay/SKILL.md).
- Configure/run: [docs/operations.md](docs/operations.md); API: [docs/api.md](docs/api.md).
- Joint setup: [docs/codex.md](docs/codex.md).
- Receivers interpreting Telegram deliveries use the packaged
  [skills/telegram-message/SKILL.md](skills/telegram-message/SKILL.md).

## Develop

Read [docs/testing.md](docs/testing.md) and [tools/README.md](tools/README.md).
`tools/` contains developer checks, schema export and demonstrations, not a
second runtime. Run `uv run --locked python tools/check.py` before code commits.

## Rules

- All published text files use English and ASCII, including fixtures and commits.
- Owned binary documentation images must satisfy the narrow asset checks in
  tools/check.py and the documented demo review; other files remain text-checked.
- Work on a feature branch. Open or merge PRs only with explicit owner authorization.
- Do not publish releases without explicit owner authorization.
- Keep KISS, DRY, SOLID and YAGNI. One bot per daemon; no storage framework.
- Preserve every raw inbound update with its access decision in JSONL.
- Confirm Telegram updates only after durable journal commit.
- Rotate directories; closed JSONL ends with a continuation link.
- Gateway is optional. Its acceptance is not model completion.
- REST and Streamable HTTP MCP share application operations in one process.
- Archive outbound operation requests and results, never secrets.
- Run `uv run --locked python tools/check.py` before every code commit.
- Test error paths and recovery, not only happy paths; real bot tests opt in.
- Use project-only commit messages, without attribution or co-author trailers.
