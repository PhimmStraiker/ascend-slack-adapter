"""
Ascend Proxy Lambda — Unified API for any chatbot.

Ascend sends: POST /chat { "prompt": "...", "adapter": "browser", "config_name": "customer-name" }
Lambda returns: { "response": "...", "{{ RESPONSE }}": "..." }

Supports six adapter types:
  - direct_api     : Direct HTTP call (any stateless REST endpoint)
  - session_api    : Session-based API (Agentforce Agent API, Bedrock agents, Azure AI)
  - browser        : Playwright headless Chromium (embedded chat widgets)
  - amazon_connect : Amazon Connect Chat widget (JWT → WebSocket flow)
  - scrt2_direct   : Salesforce Agentforce SCRT2 REST + SSE (no browser, ~4-6s/prompt)
  - slack_direct   : Slack bot DM via xoxc/xoxd Web API polling
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from adapters import BrowserAdapter, DirectAPIAdapter, SessionAPIAdapter, AmazonConnectAdapter, SCRT2DirectAdapter, SlackDirectAdapter

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CONFIGS_DIR = Path(__file__).parent / "configs"

ADAPTER_CLASSES = {
    "browser": BrowserAdapter,
    "direct_api": DirectAPIAdapter,
    "session_api": SessionAPIAdapter,
    "amazon_connect": AmazonConnectAdapter,
    "scrt2_direct": SCRT2DirectAdapter,
    "slack_direct": SlackDirectAdapter,
}

# Persistent adapter instances — browser adapter reuses its Chromium session
_adapter_instances: Dict[str, Any] = {}


def load_config(name: str) -> Dict[str, Any]:
    """Load a customer config file by name (e.g. 'acme' -> configs/acme.json)."""
    config_path = CONFIGS_DIR / f"{name}.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Config '{name}' not found at {config_path}")
    with open(config_path) as f:
        return json.load(f)


def lambda_handler(event, context):
    """AWS Lambda entry point — also used by local_server.py for local testing."""
    try:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

        prompt = body.get("prompt") or body.get("message") or body.get("{{ PROMPT }}")
        adapter_type = body.get("adapter", "")
        config = body.get("config", {})
        config_name = body.get("config_name")

        if not prompt:
            return _response(400, {"error": "No prompt provided"})

        if config_name and not config:
            try:
                config = load_config(config_name)
            except FileNotFoundError as e:
                return _response(404, {"error": str(e)})

        # Fall back to adapter declared in config file if not specified in request body
        if not adapter_type:
            adapter_type = config.get("adapter", "browser")

        adapter_cls = ADAPTER_CLASSES.get(adapter_type)
        if not adapter_cls:
            return _response(400, {
                "error": f"Unknown adapter: {adapter_type}",
                "available": list(ADAPTER_CLASSES.keys()),
            })

        cache_key = f"{adapter_type}:{config_name or 'inline'}"
        if cache_key not in _adapter_instances:
            _adapter_instances[cache_key] = adapter_cls()
        adapter = _adapter_instances[cache_key]
        result = asyncio.run(adapter.send_prompt(prompt, config))

        if not result.get("success"):
            return _response(502, {
                "error": result.get("error", "Unknown adapter error"),
                "duration_ms": result.get("duration_ms"),
                "metadata": result.get("metadata", {}),
            })

        return _response(200, {
            "response": result["response"],
            "{{ RESPONSE }}": result["response"],
            "duration_ms": result["duration_ms"],
            "metadata": result.get("metadata", {}),
            "timestamp": datetime.utcnow().isoformat(),
        })

    except Exception as e:
        logger.error(f"Lambda handler error: {e}", exc_info=True)
        return _response(500, {"error": f"Internal error: {str(e)}"})


def _response(status: int, body: Dict) -> Dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
        "body": json.dumps(body),
    }


if __name__ == "__main__":
    import sys
    adapter = sys.argv[1] if len(sys.argv) > 1 else "browser"
    config_name = sys.argv[2] if len(sys.argv) > 2 else "example"
    prompt = sys.argv[3] if len(sys.argv) > 3 else "What can you help me with?"

    test_event = {
        "body": json.dumps({
            "prompt": prompt,
            "adapter": adapter,
            "config_name": config_name,
        })
    }

    result = lambda_handler(test_event, None)
    print(json.dumps(json.loads(result["body"]), indent=2))
