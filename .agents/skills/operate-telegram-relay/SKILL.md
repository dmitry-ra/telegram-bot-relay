---
name: operate-telegram-relay
description: Configure, run, inspect or troubleshoot a Telegram Bot Relay instance from this repository; use the separate telegram-message skill to interpret delivered Telegram events.
---

# Operate Telegram Bot Relay

Relay archives incoming bot updates and files, and serves HTTP/MCP operations
for sending messages, replies, files and reactions. It works without Gateway;
optional forwarding connects the archive to a configured Gateway target.

Read the repository [AGENTS.md](../../../AGENTS.md). Choose the relevant guide;
these links are relative to this skill's directory and also work for agents
that follow the AGENTS entry point without automatic skill discovery.

- Configure a bot or run/stop/update an instance:
  [operations](../../../docs/operations.md#configure) and the public
  [configuration example](../../../examples/config.toml). Identify the requested instance and configuration from the task and existing
  documented setup; ask only if that target remains ambiguous. A repository
  checkout alone does not authorize starting another poller.
- Send or inspect API operations: [HTTP/MCP contracts](../../../docs/api.md).
  A successful HTTP request is not a successful Telegram send: inspect the
  outcome status and returned message ID. An uncertain send must not be
  automatically replayed.
- Investigate archive, restart, attachment or forwarding behavior:
  [architecture](../../../docs/architecture.md). Preserve raw updates and
  archive references; Gateway acceptance is queue admission, not model completion.
- Connect a conversation in both directions:
  [Telegram + Gateway cookbook](../../../docs/codex.md). It owns the complete
  setup; use the receiver skill linked from AGENTS for original event semantics.
- Verify commands and make changes: `uv run --locked telegram-bot-relay --help`,
  `--version`, [testing](../../../docs/testing.md), and
  [the credential-free demo](../../../docs/demo.md).

Use the requested instance and existing authorization. Keep credential values
out of output and commits. Check the bot's polling owner before startup and
use separate credentials/archives for separate instances. Health reports HTTP
availability, not Telegram reachability; verify the requested direction with
archive records or a real operation receipt as appropriate.

Use synthetic endpoints for documentation/testing unless a live bot trial is
explicitly requested. The demo proves the local Relay/MCP path, not live
Telegram delivery. The guides describe supported commands; no host-specific
service names, chat IDs or local deployment bindings are implied.
