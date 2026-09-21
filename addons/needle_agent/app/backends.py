"""Conversation-Backends.

Drei austauschbare Backends:

* ``needle``  – Needle 3, schnell, nur Tool-Calls (keine freien Antworten).
* ``openai``  – beliebiger **OpenAI-kompatibler** Endpunkt mit Function-Calling
  (Ollama, llama.cpp-Server, LM Studio, OpenRouter, OpenAI, ...). Das fuehlt
  sich wie eine echte Unterhaltung an.
* ``ha``      – Home Assists eigener Conversation-Agent (Basis/Fallback).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .ha_client import HomeAssistantClient
from .settings import Settings

MAX_DIRECT_TOOLS = 5

BACKENDS = ("needle", "openai", "ha")

# Sinnvolle Modelle fuer den OpenAI-kompatiblen Backend (Ollama-Tags).
OPENAI_MODEL_CATALOG = [
    {"id": "qwen3:1.7b", "label": "Qwen3 1.7B – empfohlen (Tool-Calling, ~1.4 GB)"},
    {"id": "qwen3:0.6b", "label": "Qwen3 0.6B – Pi-freundlich (~0.5 GB)"},
    {"id": "qwen2.5:1.5b-instruct", "label": "Qwen2.5 1.5B Instruct (~1 GB)"},
    {"id": "llama3.2:1b", "label": "Llama 3.2 1B (~0.8 GB)"},
    {"id": "llama3.2:3b", "label": "Llama 3.2 3B (~2 GB)"},
    {"id": "gemma3:1b", "label": "Gemma 3 1B (~0.8 GB)"},
    {"id": "phi4-mini", "label": "Phi-4 mini 3.8B – stark (~2.5 GB)"},
    {"id": "qwen3:4b", "label": "Qwen3 4B – beste Qualität (~2.6 GB)"},
    {"id": "functiongemma:270m", "label": "FunctionGemma 270M – nur Tool-Calling"},
]


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


# ---------------------------------------------------------------------------
# Needle 3
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# OpenAI-kompatibel (Ollama, llama.cpp, LM Studio, OpenRouter, OpenAI, ...)
# ---------------------------------------------------------------------------
class OpenAIBackend:
    name = "openai"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        temperature: float = 0.2,
        max_tokens: int = 256,
        timeout: float = 120.0,
        logger=print,
    ) -> None:
        if not base_url:
            raise ValueError("openai_base_url ist nicht gesetzt")
        if not model:
            raise ValueError("openai_model ist nicht gesetzt")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._logger = logger
        self._messages: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._pending: list[ToolCall] = []

    def reset(self) -> None:
        self._messages = []
        self._pending = []

    async def begin(self, text: str, system: str, tools: list[dict[str, Any]]) -> Decision:
        self.reset()
        self._tools = tools
        self._messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ]
        return await self._chat()

    async def step(self, results: list[Any]) -> Decision:
        for call, result in zip(self._pending, results):
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id or call.name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        return await self._chat()

    async def _chat(self) -> Decision:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if self._tools:
            payload["tools"] = [{"type": "function", "function": t} for t in self._tools]
        response = await self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        message = (data.get("choices") or [{}])[0].get("message") or {}
        self._messages.append(message)

        raw_calls = message.get("tool_calls") or []
        if raw_calls:
            calls: list[ToolCall] = []
            for raw in raw_calls:
                function = raw.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                calls.append(ToolCall(str(function.get("name", "")), arguments, raw.get("id")))
            self._pending = calls
            return Decision(calls=calls, raw=data)

        text = (message.get("content") or "").strip()
        return Decision(calls=[], text=text or None, raw=data)

    async def close(self) -> None:
        await self._client.aclose()

    def info(self) -> dict[str, Any]:
        return {"backend": self.name, "model": self._model, "tools": len(self._tools)}


# ---------------------------------------------------------------------------
# Home Assistant (eigener Agent)
# ---------------------------------------------------------------------------
class HABackend:
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
    backend = (settings.backend or "needle").lower()
    if backend == "openai":
        return OpenAIBackend(
            settings.openai_base_url,
            settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=settings.openai_temperature,
            max_tokens=settings.openai_max_tokens,
            logger=logger,
        )
    if backend == "ha":
        return HABackend(ha, settings.language, logger=logger)
    return NeedleBackend(
        tool_schemas,
        system=system,
        tool_index_path=tool_index_path,
        max_new_tokens=settings.needle_max_tokens,
    )
