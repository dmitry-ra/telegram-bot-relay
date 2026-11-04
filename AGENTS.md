# Development rules

- All published files use English and ASCII, including fixtures and commits.
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
