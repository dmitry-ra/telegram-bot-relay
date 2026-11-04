# telegram-bot-relay

A standalone Telegram bot relay with rotating JSONL archives, HTTP and MCP
messaging, and optional Message Gateway delivery.

One daemon owns one bot. All incoming updates are archived with an access
decision, including denied messages. Attachments rotate with their message
period. REST and Streamable HTTP MCP share outgoing operations and archive
their requests/results. The bot keeps collecting when Gateway is disabled
or unavailable.

Built with Python>=3.12, aiogram3.31.0 and the MCP SDK. No database server,
separate MCP proxy, automatic transcription or model provider is required.

```sh
uv sync --locked
uv run --locked telegram-bot-relay --config /absolute/private-config.toml
```

Configure examples/config.toml first. Provide private bot/API token files and
numeric user/outgoing-chat allowlists. Default private policy denies everyone.
See [operations](docs/operations.md), [API](docs/api.md) and
[architecture](docs/architecture.md). The packaged Telegram skill explains
incoming full context and correct reply/attachment handling.

For a complete bidirectional setup with your own bot and an interactive Codex
conversation, follow [Connect a bot to Codex](docs/codex.md). It uses ordinary
configuration files and the public projects, without a host-specific helper.

Run uv run --locked python tools/check.py before code commits. CI checks
Python3.12/3.13/3.14, formatting, lint, strict types, schemas and branch coverage.
Real bot tests are opt-in. This project is under initial development; no PyPI
release is published by repository setup.
