# Ascend Slack Adapter

A Straiker Ascend FDE adapter for red-teaming AI agents deployed as Slack bots.

Built and validated against an internal enterprise Slack bot that calls workplace tools and replies in a thread.

---

## Overview

The `slack_direct` adapter enables Ascend's automated adversarial testing against any Slack bot the authenticated user can DM. It uses the Slack Web API with a standard user OAuth token (`xoxp-`) — no browser automation, no session cookies.

```
Ascend Console (cloud)
      ↓  adversarial prompts
ascend-bridge (local binary)
      ↓  POST /chat
AscendProxy local_server.py  ← integrates this adapter
      ↓  Slack Web API (xoxp token)
Target Slack Bot
```

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| `xoxp` user token (not `xoxc`/`xoxd`) | Browser session tokens expire and require full cookie jar — user OAuth tokens are long-lived and work reliably from server-side |
| `conversations.replies` polling | Many enterprise Slack bots reply in threads, not the main channel |
| Loading message filter | Multi-agent bots send a "processing..." message before the real reply — filter prevents returning incomplete responses |
| `max_workers: 1` | Slack rate limits; sequential prompts avoid 429s |

---

## Integration into ascend-fde-toolkit

Drop `adapter/slack_direct.py` into `AscendProxy/adapters/` and register it:

**`AscendProxy/adapters/__init__.py`** — add:
```python
from .slack_direct import SlackDirectAdapter
```

**`AscendProxy/lambda_function.py`** — add to `ADAPTER_CLASSES`:
```python
"slack_direct": SlackDirectAdapter,
```

Also update `lambda_function.py` to fall back to the config file's `adapter` field when none is specified in the request body:
```python
# After loading config:
if not adapter_type:
    adapter_type = config.get("adapter", "browser")
```

---

## Config reference

```json
{
  "adapter": "slack_direct",

  "slack_token": "xoxp-...",

  "channel_id": "D...",
  "bot_id": "B...",
  "user_id": "U...",

  "timeout_ms": 90000,
  "poll_interval_ms": 2000,
  "warmup_message": "Hello"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `slack_token` | Yes | `xoxp-` user OAuth token from api.slack.com/apps |
| `channel_id` | Yes | DM channel ID with the bot (`D...`) |
| `user_id` | Yes | Your Slack user ID (`U...`) — filters out self-messages |
| `bot_id` | No | Bot's `bot_id` (`B...`) — improves response filtering accuracy |
| `timeout_ms` | No | Max wait time for bot response (default: 90000) |
| `poll_interval_ms` | No | Polling interval in ms (default: 2000) |
| `warmup_message` | No | Send this first and discard response — handles bots that send a greeting on first contact |

### Ascend Console settings

```
Request template:  {"prompt":"{{PROMPT}}","config_name":"<your-config-name>"}
Response template: {"response":"{{RESPONSE}}"}
QPM: 4
max_workers: 1
```

---

## Slack app setup

See [docs/SLACK_APP_SETUP.md](docs/SLACK_APP_SETUP.md) for step-by-step instructions to create a Slack app and get a `xoxp` token.

**Required User Token Scopes:**
- `chat:write`
- `im:history`

---

## Known bot patterns

### Thread-reply bots
The bot replies in threads, not the main channel. The adapter detects this by polling `conversations.replies` on the sent message's timestamp. No config change needed — this is the default behavior.

### Two-stage response bots
Some bots send a loading/status message first, then append the real reply to the same thread. The adapter filters these using known loading signals:
- `"connecting to platforms"`
- `"might take a minute"`
- `"alert you of a new message"`

Additional signals can be added to the `loading_signals` list in `slack_direct.py`.

### Main-channel reply bots
If a bot replies in the main channel (not threads), replace `_get_replies` with `_get_history` polling in `slack_direct.py`. Filed as a future config option (`"reply_in_thread": false`).

---

## Roadmap / productization notes

This adapter is intended to be merged into the Straiker core product alongside existing platform adapters (Vertex, SCRT2, Amazon Connect). Planned improvements:

- [ ] `reply_in_thread` config flag (auto-detect or explicit)
- [ ] Configurable `loading_signals` list per engagement
- [ ] Multi-workspace support (separate token per workspace)
- [ ] Rate limit backoff (handle Slack 429s gracefully)
- [ ] Evidence capture (screenshot of Slack thread per prompt, for report appendix)

---

## Validated engagements

| Customer | Bot | Reply pattern | Notes |
|----------|-----|--------------|-------|
| Internal enterprise bot | — | Thread | Two-stage response; calls workplace tools |
