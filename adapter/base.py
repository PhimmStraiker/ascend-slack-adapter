"""
Minimal BotAdapter interface — matches the contract in ascend-fde-toolkit.

All adapters implement send_prompt() and return a standard dict.
This file is a standalone copy so the Slack adapter can be developed/tested
independently before being merged into the core toolkit.
"""

import time
from abc import ABC, abstractmethod
from typing import Any, Dict


class BotAdapter(ABC):

    @abstractmethod
    async def send_prompt(self, prompt: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a prompt to the target bot and return its response.

        Returns:
            {
                "response": str,
                "success": bool,
                "error": str | None,
                "duration_ms": int,
                "metadata": dict
            }
        """
        raise NotImplementedError

    def _ok(self, response: str, start: float, **metadata) -> Dict[str, Any]:
        return {
            "response": response,
            "success": True,
            "error": None,
            "duration_ms": int((time.time() - start) * 1000),
            "metadata": metadata,
        }

    def _fail(self, error: str, start: float, **metadata) -> Dict[str, Any]:
        return {
            "response": "",
            "success": False,
            "error": error,
            "duration_ms": int((time.time() - start) * 1000),
            "metadata": metadata,
        }
