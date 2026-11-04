# Connect a bot to an interactive Codex conversation

This recipe connects a bot you own to a conversation you also use in the
terminal. Relay receives Telegram updates, archives them and optionally passes
them to Message Gateway. Gateway queues and batches input for Codex. Codex
sends replies or proactive messages through Relay's MCP tools.

No custom Python integration, hooks, tmux automation or host-specific setup
script is required. Use the repository's existing commands and configuration.
These steps target Linux and the Gateway adapter tested with Codex 0.166.0;
the native App Server protocol is an experimental compatibility dependency.

## 1. Install and choose private paths

Clone this repository and
[Message Gateway](https://github.com/dmitry-ra/message-gateway). Run
`uv sync --locked` in each checkout. The projects are pre-release; these steps
do not depend on a published PyPI package. Record the source revisions you use.
Install and authenticate Codex separately.

Choose a private configuration directory, an archive directory, your actual
working project directory, and unused loopback ports. The examples below use
`/home/example`, Relay 10881 and Gateway 10880. Replace every example path and
numeric ID; Codex does not need a separate working directory for this bridge.

Obtain your bot's token from BotFather. Store only the token value in
`bot.token`, with mode 0600 in a directory with mode 0700. Create distinct random
`relay-api.token` and `gateway-api.token` secrets with the same permissions.
Do not commit these files or pass secrets in command arguments. Stop any other
poller for this bot before starting Relay. An existing webhook must be removed
deliberately by the bot owner; Relay does not silently take it over.

Use your numeric Telegram user/chat IDs, not a username. A private user must
have opened the bot and started a conversation before the bot can message them.

## 2. Configure Relay

Copy [examples/config.toml](../examples/config.toml) to your private directory.
Set token paths, `data_dir`, and `port`. For a private one-user bot, set
`access.allowed_users` and `access.outgoing_chats` to that user's numeric ID;
keep `public = false` and `groups = false`. Inbound permission does not grant
outgoing permission. Set attachment download policy and upload roots as needed.

Add this optional section, using a target name of your choice:

```toml
[gateway]
url = "http://127.0.0.1:10880"
target = "work"
start = "latest"
token_file = "/home/example/.config/bridge/gateway-api.token"
```

`latest` begins with future archive messages on the first delivery history;
`oldest` forwards existing archived messages too. Choose deliberately. Without
this section, Relay still archives input and provides outgoing HTTP/MCP tools.

Start from the Relay checkout:

```sh
uv run --locked telegram-bot-relay --config /home/example/.config/bridge/relay.toml
```

## 3. Connect the receiver's outgoing tools and skills

Add a project MCP entry to `.codex/config.toml` in your actual working project:

```toml
[mcp_servers.telegram_bot_relay]
url = "http://127.0.0.1:10881/mcp"
bearer_token_env_var = "TELEGRAM_RELAY_API_TOKEN"
```

Set that variable securely in the Codex launch environment to the contents of
`relay-api.token`. Alternatively Codex supports an Authorization `http_headers`
entry: treat that configuration itself as a secret and exclude it from version
control. Trust project configuration only after reviewing it. Tool approval
policy is the operator's choice, separate from Relay's destination allowlist.

Install this repository's `skills/telegram-message` in your project's
`.agents/skills/telegram-message`. Install Gateway's generic packet skill or
its plugin as described in Gateway's README. Neither skill replaces the other:
Gateway handles framing; the Telegram skill interprets original updates and
explains reply IDs and attachments. If the services run under different users
or on different hosts, arrange readable attachment paths explicitly.

Start Codex in your project. Verify the Relay MCP connection, then instruct it:

> Read the gateway-message and telegram-message skills. For incoming Telegram
> requests, read full original content from the gateway packet. Reply through
> telegram_bot_relay.reply using original chat.id and message_id as chat_id and
> reply_to. For a proactive message use send_message with the explicitly
> authorized chat ID. Console requests normally receive console answers.

This instruction can live in your project's existing instructions. No special
bootstrap API call or custom session-start script is required.

## 4. Bind incoming delivery to that conversation

In Codex, use `/status` to obtain the session UUID. Verify that it uses the
local background App Server supported by Gateway, rather than an isolated
embedded instance. Copy Gateway's `examples/gateway.toml` and set:

```toml
[[targets]]
name = "work"
thread = "REPLACE_WITH_YOUR_SESSION_UUID"
batching = true
interactive = true
```

Use Message Gateway 0.6.0 or later for this interactive configuration. Incoming
messages steer an active Codex turn and start a turn when the conversation is
not active. Batching is independent: set `batching = false` to submit each
message separately, or keep it enabled to combine pending messages. Event
consumers that should wait for the conversation to finish can omit
`interactive` or set it to false.

From the Gateway checkout, set `MESSAGE_GATEWAY_TOKEN` securely to the contents
of `gateway-api.token` and run:

```sh
uv run --locked message-gateway serve --config /home/example/.config/bridge/gateway.toml --port 10880
```

The target name must match Relay's `gateway.target`. The Gateway token must
match Relay's token file; the Relay MCP token is a different credential.

## 5. Verify both directions and keep them running

Ask Codex in the terminal to send a short marker through Relay MCP to your chat.
Check the tool outcome is `sent` and inspect Telegram's returned message ID.
Send a new question from your Telegram account to the bot. Confirm its preview
appears in the intended Codex terminal and the answer arrives in Telegram as a
reply to that question. Also test an attachment and several messages arriving
while Codex is busy. HTTP success or Gateway acceptance alone is insufficient.

For unattended operation use separate service instances. This repository has
an optional systemd template; process supervision is not part of message
routing. For a second bot use another Relay instance with its own token,
archive directory, API secret, port and receiver configuration. A Gateway can
hold multiple named targets, or instances can be separated for isolation.

Resume the same session UUID to keep the route. A newly created conversation
needs an explicit target update and Gateway restart; automatic binding is not
implemented. A loaded conversation can outlive its terminal. Gateway's accepted
queue is memory-only: a restart can lose accepted but undelivered messages even
though Relay's source archive remains. Native idle checks cannot atomically
exclude concurrent keyboard submissions. See Gateway's README for limits.

Stop Relay before handing the bot back to another consumer. Preserve archives
unless their deletion was explicitly intended. For troubleshooting inspect
Relay's authenticated `/healthz`, Gateway's `/v1/targets`, journal access /
gateway acceptance / operation outcomes, and the Codex MCP connection.
