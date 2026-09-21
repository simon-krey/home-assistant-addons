"""Backends.

Es gibt nur noch **Needle 3** als Modell. ``HABackend`` bleibt als
Sicherheitsnetz (Fallback), wenn Needle nichts Brauchbares liefert – es ist
kein eigenes Modell, sondern Home Assists eigener Agent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from .ha_client import HomeAssistantClient
from .settings import Settings

MAX_DIRECT_TOOLS = 5

BACKENDS = ("needle",)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None


@dataclass
class Decision:
    calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    confidence: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Backend(Protocol):
    name: str

    async def begin(self, text: str, system: str, tools: list[dict[str, Any]]) -> Decision: ...

    async def step(self, results: list[Any]) -> Decision: ...

    async def close(self) -> None: ...

    def reset(self) -> None: ...

    def info(self) -> dict[str, Any]: ...


class NeedleBackend:
    name = "needle"

    def __init__(
        self,
        tools: list[dict[str, Any]],
        *,
        system: str | None = None,
        tool_index_path: str | None = None,
        max_new_tokens: int = 256,
    ) -> None:
        import needle

        kwargs: dict[str, Any] = {"tools": tools}
        if system:
            kwargs["system"] = system
        if len(tools) > MAX_DIRECT_TOOLS and tool_index_path:
            kwargs["tool_index_path"] = tool_index_path
        self.agent = needle.Needle(**kwargs)
        self.max_new_tokens = max_new_tokens
        self._tools = tools

    def reset(self) -> None:
        self.agent.reset()

    async def begin(self, text: str, system: str, tools: list[dict[str, Any]]) -> Decision:
        self.agent.reset()
        return self._decision(self.agent.complete(text=text, max_new_tokens=self.max_new_tokens))

    async def step(self, results: list[Any]) -> Decision:
        payload: Any = results[0] if len(results) == 1 else results
        return self._decision(
            self.agent.complete(
                text=json.dumps(payload, ensure_ascii=False),
                max_new_tokens=self.max_new_tokens,
            )
        )

    async def close(self) -> None:
        return None

    def info(self) -> dict[str, Any]:
        return {"backend": self.name, "tools": len(self._tools)}

    def _decision(self, response: dict[str, Any]) -> Decision:
        calls = [
            ToolCall(str(c.get("name", "")), dict(c.get("arguments") or {}))
            for c in (response.get("function_calls") or [])
        ]
        confidence = response.get("confidence")
        return Decision(
            calls=calls,
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            raw=response,
        )


class HABackend:
    """Home Assists eigener Conversation-Agent (Fallback, kein eigenes Modell)."""

    name = "ha"

    def __init__(self, ha: HomeAssistantClient, language: str = "de", logger=print) -> None:
        self._ha = ha
        self._language = language
        self._logger = logger

    def reset(self) -> None:
        return None

    async def begin(self, text: str, system: str, tools: list[dict[str, Any]]) -> Decision:
        reply = await self._ha.converse(text, self._language)
        return Decision(calls=[], text=reply or None)

    async def step(self, results: list[Any]) -> Decision:
        return Decision(calls=[])

    async def close(self) -> None:
        return None

    def info(self) -> dict[str, Any]:
        return {"backend": self.name, "language": self._language}


def build_backend(
    settings: Settings,
    ha: HomeAssistantClient,
    tool_schemas: list[dict[str, Any]],
    *,
    tool_index_path: str | None = None,
    system: str,
    logger=print,
) -> Backend:
    if (settings.backend or "needle").lower() == "ha":
        return HABackend(ha, settings.language, logger=logger)
    return NeedleBackend(
        tool_schemas,
        system=system,
        tool_index_path=tool_index_path,
        max_new_tokens=settings.needle_max_tokens,
    )
