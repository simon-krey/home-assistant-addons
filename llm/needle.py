"""Needle-3-Anbindung.

Nutzt bewusst ``complete()`` statt ``run()``: Der Tool-Dispatcher soll die
Kontrolle behalten und das Ergebnis selbst zurueckfuettern.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config
from tools.registry import ToolRegistry


class NeedleAgent:
    def __init__(
        self,
        registry: ToolRegistry,
        system: str | None = None,
        max_new_tokens: int | None = None,
        tool_index_path: str | Path | None = None,
    ) -> None:
        import needle  # schwerer Import, erst zur Laufzeit

        self.registry = registry
        self.max_new_tokens = max_new_tokens or config.NEEDLE_MAX_NEW_TOKENS
        self.system = system if system is not None else config.NEEDLE_SYSTEM

        kwargs: dict[str, Any] = {"tools": registry.schemas()}
        if self.system:
            kwargs["system"] = self.system

        index_path = (
            Path(tool_index_path)
            if tool_index_path is not None
            else config.NEEDLE_TOOL_INDEX_PATH
        )
        # Ab mehr als fuenf Tools greift Needles Tool-Retrieval.
        if len(registry) > 5 and index_path is not None:
            index_path = Path(index_path)
            index_path.parent.mkdir(parents=True, exist_ok=True)
            kwargs["tool_index_path"] = str(index_path)

        self.agent = needle.Needle(**kwargs)

    def complete(self, text: str) -> dict[str, Any]:
        return self.agent.complete(text=text, max_new_tokens=self.max_new_tokens)

    def feed_result(self, result: Any) -> dict[str, Any]:
        """Tool-Ergebnis als naechste Runde zurueckgeben (Result-Flow)."""
        return self.complete(json.dumps(result, ensure_ascii=False))

    def reset(self) -> None:
        self.agent.reset()


# -- Response-Helfer ------------------------------------------------------
def function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    return list(response.get("function_calls") or [])


def suppressed_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    return list(response.get("suppressed_calls") or [])


def is_refusal(response: dict[str, Any]) -> bool:
    """Kein passendes Tool: Needle antwortet mit leerer Call-Liste."""
    return response.get("type") == "call" and not function_calls(response)


def is_respond(response: dict[str, Any]) -> bool:
    return response.get("type") == "respond"


def confidence(response: dict[str, Any]) -> float | None:
    value = response.get("confidence")
    return float(value) if isinstance(value, (int, float)) else None


def is_low_confidence(response: dict[str, Any], threshold: float) -> bool:
    value = confidence(response)
    return value is not None and value < threshold
