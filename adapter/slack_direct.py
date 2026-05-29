"""
Slack Direct adapter — Slack Web API using a xoxp user OAuth token.

Sends prompts to a Slack bot DM channel and polls conversations.replies for the response.
No browser required. Works against any Slack bot the authenticated user can DM.

Auth: Standard Bearer token (xoxp-...) — create via api.slack.com/apps,
      add User Token Scopes: chat:write, im:history, install to workspace.

Flow per prompt:
  1. POST /api/chat.postMessage  → send prompt to bot's DM channel
  2. Poll GET /api/conversations.replies?ts=<sent_ts>  → wait for bot reply in thread
  3. Skip loading/status messages (two-stage response bots)
  4. Extract text from Slack Block Kit blocks or plain text field
  5. Return response

Required proxy setup (local_server.py):
  - Use ThreadingHTTPServer (not HTTPServer) for concurrent bridge workers
  - Stream HTTP headers immediately before calling adapter (prevents bridge timeout)
  - Send keepalive whitespace chunks every 10s while waiting (prevents body-read timeout)
  - Use asyncio.run() not asyncio.get_event_loop() (safe in threaded context)
  See README.md § Proxy Requirements for full patch.

Config keys:
  slack_token      - User OAuth token (xoxp-...) from api.slack.com/apps
  channel_id       - DM channel ID with the bot (D...)
  user_id          - Your Slack user ID (U...) to filter out self-messages
  bot_id           - Bot's bot_id (B...) for reliable response filtering (optional)
  timeout_ms       - Max wait time for bot response in ms (default 90000)
  poll_interval_ms - How often to check for new replies in ms (default 2000)
  warmup_message   - Send this first and discard response (handles greeting flows)
  loading_signals  - List of strings indicating a loading/status message (optional)
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from .base import BotAdapter

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"

DEFAULT_LOADING_SIGNALS = [
    "connecting to platforms",
    "might take a minute",
    "alert you of a new message",
]


class SlackDirectAdapter(BotAdapter):
    """Slack Web API adapter — DM thread polling via xoxp user OAuth token."""

    def __init__(self):
        self._warmed_up = False

    def _headers(self, token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _post_message(self, channel: str, text: str, token: str, timeout: float) -> str:
        """Post a message to a channel. Returns the message ts."""
        body = json.dumps({"channel": channel, "text": text}).encode()
        req = urllib.request.Request(
            f"{SLACK_API}/chat.postMessage",
            data=body,
            headers=self._headers(token),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("ok"):
            raise RuntimeError(f"chat.postMessage failed: {data.get('error', 'unknown')}")
        return data["ts"]

    def _get_replies(self, channel: str, thread_ts: str, token: str, timeout: float) -> List[Dict]:
        """Fetch thread replies. First message is the parent (our prompt) — skip it."""
        params = urllib.parse.urlencode({"channel": channel, "ts": thread_ts})
        req = urllib.request.Request(
            f"{SLACK_API}/conversations.replies?{params}",
            headers=self._headers(token),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("ok"):
            raise RuntimeError(f"conversations.replies failed: {data.get('error', 'unknown')}")
        return data.get("messages", [])[1:]

    def _extract_text(self, msg: Dict) -> str:
        """Extract readable text from a Slack message (Block Kit or plain text)."""
        blocks = msg.get("blocks", [])
        if blocks:
            parts = []
            for block in blocks:
                btype = block.get("type", "")
                if btype in ("section", "header", "context"):
                    t = block.get("text", {})
                    if t.get("text"):
                        parts.append(t["text"])
                elif btype == "rich_text":
                    for el in block.get("elements", []):
                        chunk = "".join(
                            sub.get("text", "")
                            for sub in el.get("elements", [])
                            if sub.get("type") == "text"
                        )
                        if chunk:
                            parts.append(chunk)
            if parts:
                return "\n".join(p for p in parts if p.strip())

        text = msg.get("text", "")
        if text:
            return text

        for att in msg.get("attachments", []):
            t = att.get("text") or att.get("fallback", "")
            if t:
                return t

        return ""

    def _is_bot_response(self, msg: Dict, user_id: str, bot_id: str) -> bool:
        if msg.get("user") == user_id:
            return False
        if bot_id and msg.get("bot_id") == bot_id:
            return True
        if msg.get("bot_id"):
            return True
        if msg.get("subtype") == "bot_message":
            return True
        return False

    def _poll_for_reply(
        self,
        channel: str,
        sent_ts: str,
        token: str,
        user_id: str,
        bot_id: str,
        poll_interval: float,
        http_timeout: float,
        deadline: float,
        loading_signals: List[str],
    ) -> str:
        # Stability check: require the candidate message to be unchanged across two
        # consecutive polls before returning. Handles thinking/editing messages that
        # don't match the loading_signals list — they'll settle before we commit.
        last_candidate_ts = None
        last_candidate_text = None
        attempts = 0

        while time.time() < deadline:
            time.sleep(poll_interval)
            attempts += 1
            replies = self._get_replies(channel, sent_ts, token, http_timeout)
            bot_msgs = [m for m in replies if self._is_bot_response(m, user_id, bot_id)]
            if not bot_msgs:
                last_candidate_ts = None
                last_candidate_text = None
                continue

            final_msgs = [
                m for m in bot_msgs
                if not any(sig in self._extract_text(m).lower() for sig in loading_signals)
            ]
            target = final_msgs[-1] if final_msgs else None
            if not target:
                last_candidate_ts = None
                last_candidate_text = None
                continue

            text = self._extract_text(target)
            if not text.strip():
                continue

            if target["ts"] == last_candidate_ts and text == last_candidate_text:
                logger.info(f"SlackDirect: stable reply after {attempts} polls ({len(text)} chars)")
                return text

            last_candidate_ts = target["ts"]
            last_candidate_text = text

        return ""

    async def send_prompt(self, prompt: str, config: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()

        token = config.get("slack_token", "")
        # Support both generic key names and legacy legacy names
        channel = config.get("channel_id") or config.get("channel_id", "")
        user_id = config.get("user_id", "")
        bot_id = config.get("bot_id") or config.get("bot_id", "")
        timeout_ms = config.get("timeout_ms", 90000)
        poll_interval = config.get("poll_interval_ms", 2000) / 1000
        warmup_message = config.get("warmup_message", "")
        loading_signals = config.get("loading_signals", DEFAULT_LOADING_SIGNALS)
        http_timeout = 30.0  # per-call Slack API timeout (not total adapter timeout)

        if not all([token, channel, user_id]):
            return self._fail("Missing required config: slack_token, channel_id, user_id", start)

        try:
            if warmup_message and not self._warmed_up:
                logger.info("SlackDirect: sending warmup message")
                try:
                    wts = self._post_message(channel, warmup_message, token, http_timeout)
                    self._poll_for_reply(
                        channel, wts, token, user_id, bot_id,
                        poll_interval, http_timeout, time.time() + 30,
                        loading_signals,
                    )
                    time.sleep(1.0)
                except Exception as e:
                    logger.warning(f"SlackDirect: warmup failed (non-fatal): {e}")
                finally:
                    self._warmed_up = True

            logger.info(f"SlackDirect: posting prompt ({len(prompt)} chars) to {channel}")
            sent_ts = self._post_message(channel, prompt, token, http_timeout)
            logger.info(f"SlackDirect: sent ts={sent_ts}, polling for reply...")

            text = self._poll_for_reply(
                channel, sent_ts, token, user_id, bot_id,
                poll_interval, http_timeout,
                time.time() + timeout_ms / 1000,
                loading_signals,
            )

            if not text:
                return self._fail(
                    f"Timeout ({timeout_ms}ms): no response from bot in channel {channel}",
                    start, adapter="slack_direct", channel=channel,
                )
            return self._ok(text, start, adapter="slack_direct", channel=channel)

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            logger.error(f"SlackDirect HTTP {e.code}: {body}")
            return self._fail(f"HTTP {e.code}: {body}", start, adapter="slack_direct")
        except Exception as e:
            logger.error(f"SlackDirect error: {e}", exc_info=True)
            return self._fail(str(e), start, adapter="slack_direct")
