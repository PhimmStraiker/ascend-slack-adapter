# Slack App Setup Guide

Creating a Slack app takes ~2 minutes and gives you a long-lived `xoxp` user token that works reliably with the Slack Web API. This is required for the `slack_direct` adapter.

## Step 1 — Create the App

1. Go to **https://api.slack.com/apps**
2. Click **Create New App** → **From Scratch**
3. Name it (e.g. `AscendProxy`) and select the target Slack workspace
4. Click **Create App**

## Step 2 — Add Scopes

1. In the left sidebar, click **OAuth & Permissions**
2. Scroll to **Scopes** → **User Token Scopes**
3. Click **Add an OAuth Scope** and add:
   - `chat:write` — post messages to the bot's DM channel
   - `im:history` — read the DM channel history (required for conversations.replies)

> **User Token Scopes** (not Bot Token Scopes) — messages must appear as you, not as a bot app.

## Step 3 — Install to Workspace

1. Scroll back to the top of the OAuth & Permissions page
2. Click **Install to Workspace**
3. Review permissions → click **Allow**
4. Copy the **User OAuth Token** — it starts with `xoxp-`

## Step 4 — Collect IDs

You need three IDs from the target Slack workspace:

### Your user ID (`user_id`)
In Chrome DevTools Network tab, look for any `timing?user_id=U...` request URL.  
Or: click your profile picture in Slack → the URL will contain your user ID.

### Bot's channel ID (`channel_id`)
Open the DM with the target bot in Slack web app. The URL will be:  
`https://app.slack.com/client/<WORKSPACE>/<CHANNEL>`  
The `D...` value at the end is the channel ID.

### Bot's bot_id (`bot_id`)
Run this one-liner after you have your `xoxp` token and channel ID:

```bash
python3 -c "
import urllib.request, urllib.parse, json

TOKEN = 'xoxp-YOUR-TOKEN-HERE'
CHANNEL = 'D...'

params = urllib.parse.urlencode({'channel': CHANNEL, 'limit': 10})
req = urllib.request.Request(
    f'https://slack.com/api/conversations.history?{params}',
    headers={'Authorization': f'Bearer {TOKEN}'},
)
with urllib.request.urlopen(req) as r:
    msgs = json.loads(r.read()).get('messages', [])
for m in msgs:
    if m.get('bot_id'):
        print('bot_id:', m['bot_id'], '| text:', m.get('text','')[:60])
"
```

This prints the `bot_id` for every bot message in the channel. Match it to the target bot.

## Step 5 — Determine Reply Pattern

Some bots reply in the **main channel**, others reply in **threads**.  
Run the script above and check the `thread_ts` field in each message:
- If a bot message has `thread_ts` != its own `ts` → bot replies in threads
- The `slack_direct` adapter handles both automatically

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `missing_scope` | Token lacks required scope | Add scope in OAuth & Permissions, reinstall app |
| `channel_not_found` | Wrong channel ID or app not in channel | Verify channel ID from URL |
| `not_in_channel` | App not a member of the DM | Open the DM manually first so Slack creates the channel |
| Timeout with no response | Bot replies in thread, adapter polling wrong endpoint | Check `thread_ts` — if bot uses threads, `conversations.replies` is already used |
| Loading message returned | Bot has two-stage response | Loading message filter handles this — wait for final reply |
