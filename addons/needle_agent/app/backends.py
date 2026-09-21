"""Conversation-Backends.

Drei austauschbare Backends:

* ``needle``  – Needle 3, schnell, nur Tool-Calls (keine freien Antworten).
* ``openai``  – beliebiger **OpenAI-kompatibler** Endpunkt mit Function-Calling
  (Ollama, llama.cpp-Server, LM Studio, OpenRouter, OpenAI, ...). Das fuehlt
  sich wie eine echte Unterhaltung an.
* ``ha``      – Home Assists eigener Conversation-Agent (Basis/Fallback).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from . import llama_models
from .ha_client import HomeAssistantClient
from .settings import Settings

MAX_DIRECT_TOOLS = 5

BACKENDS = ("needle", "llama_cpp", "openai", "ha")

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


def _extract_json_objects(text: str) -> list[str]:
    """Alle balancierten ``{...}``-Objekte aus einem Text ziehen."""
    objects: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "{":
            depth = 0
            for position in range(index, len(text)):
                if text[position] == "{":
                    depth += 1
                elif text[position] == "}":
                    depth -= 1
                    if depth == 0:
                        objects.append(text[index : position + 1])
                        index = position
                        break
            else:
                break
        index += 1
    return objects


def _first_json(block: str) -> dict[str, Any] | None:
    for candidate in _extract_json_objects(block):
        variants: list[str] = [candidate]
        if candidate.startswith("{{"):
            variants.append(candidate[1:])
        if candidate.endswith("}}"):
            variants.append(candidate[:-1])
        if candidate.startswith("{{") and candidate.endswith("}}"):
            variants.append(candidate[1:-1])
        variants.append(candidate.replace("{{", "{").replace("}}", "}"))
        for variant in variants:
            try:
                data = json.loads(variant)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("name"):
                return data
    return None


def _coerce(value: str) -> Any:
    text = value.strip()
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "none"):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


_FUNCTION_XML_RE = re.compile(r"<function=([^>\s]+)>(.*?)</function>", re.S)
_PARAM_XML_RE = re.compile(r"<parameter=([^>\s]+)>(.*?)</parameter>", re.S)


def _parse_function_xml_calls(text: str) -> list[ToolCall]:
    """Neues Qwen-XML-Format:
    ``<function=name><parameter=key>value</parameter></function>``.
    """
    calls: list[ToolCall] = []
    for function in _FUNCTION_XML_RE.finditer(text or ""):
        name = function.group(1).strip()
        arguments: dict[str, Any] = {}
        for parameter in _PARAM_XML_RE.finditer(function.group(2)):
            arguments[parameter.group(1).strip()] = _coerce(parameter.group(2))
        if name:
            calls.append(ToolCall(name, arguments))
    return calls


def _parse_text_tool_calls(text: str) -> list[ToolCall]:
    """Tool-Calls aus Text ziehen.

    Unterstuetzt JSON (``<tool_call>{...}</tool_call>``, Qwen2.5/Qwen3/Hermes)
    und das XML-Format (``<function=…><parameter=…>…``, Qwen3.5).
    llama-cpp-python parst nicht jedes Modell-Format strukturiert.
    """
    calls: list[ToolCall] = []
    for block in re.findall(r"<tool_call>(.*?)</tool_call>", text or "", re.S):
        data = _first_json(block)
        if not data:
            continue
        arguments = data.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        calls.append(ToolCall(str(data["name"]), dict(arguments or {})))
    if calls:
        return calls
    return _parse_function_xml_calls(text)


# ---------------------------------------------------------------------------
# Eingebauter llama.cpp-Backend (GGUF, Auto-Download)
# ---------------------------------------------------------------------------
class LlamaCppBackend:
    name = "llama_cpp"

    def __init__(
        self,
        spec: "llama_models.LlamaModel",
        *,
        models_dir: str | None = None,
        n_ctx: int = 4096,
        n_threads: int = 0,
        n_gpu_layers: int = 0,
        temperature: float = 0.2,
        max_tokens: int = 256,
        disable_thinking: bool = True,
        logger=print,
    ) -> None:
        self._spec = spec
        self._models_dir = models_dir
        self._n_ctx = n_ctx
        self._n_threads = n_threads or max(1, (os.cpu_count() or 4) - 1)
        self._n_gpu_layers = n_gpu_layers
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._disable_thinking = disable_thinking
        # Qwen3 (<= 3.x) nutzt /no_think; Qwen3.5 steuert das per Template-Variable
        # (enable_thinking, default aus) und wird von /no_think nur verwirrt.
        name = spec.filename.lower()
        self._no_think = disable_thinking and "qwen3" in name and "qwen3.5" not in name
        self._logger = logger
        self._llm: Any = None
        self._load_lock = threading.Lock()
        self._messages: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._pending: list[ToolCall] = []

    def reset(self) -> None:
        self._messages = []
        self._pending = []

    @property
    def loaded(self) -> bool:
        return self._llm is not None

    def _ensure_loaded(self) -> Any:
        if self._llm is not None:
            return self._llm
        with self._load_lock:
            if self._llm is not None:
                return self._llm
            path = llama_models.ensure_model(self._spec, self._models_dir, self._logger)
            from llama_cpp import Llama

            self._logger(
                f"[LLAMA] Lade {path.name} (ctx={self._n_ctx}, threads={self._n_threads}, gpu_layers={self._n_gpu_layers})"
            )
            started = time.perf_counter()
            self._llm = Llama(
                model_path=str(path),
                n_ctx=self._n_ctx,
                n_threads=self._n_threads,
                n_gpu_layers=self._n_gpu_layers,
                verbose=False,
            )
            self._logger(f"[LLAMA] Modell geladen in {time.perf_counter() - started:.1f}s")
            return self._llm

    async def begin(self, text: str, system: str, tools: list[dict[str, Any]]) -> Decision:
        self.reset()
        self._tools = tools
        self._messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": text + (" /no_think" if self._no_think else "")},
        ]
        return await asyncio.to_thread(self._chat)

    async def step(self, results: list[Any]) -> Decision:
        for call, result in zip(self._pending, results):
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id or call.name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        return await asyncio.to_thread(self._chat)

    def _chat(self) -> Decision:
        llm = self._ensure_loaded()
        kwargs: dict[str, Any] = {
            "messages": self._messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if self._tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in self._tools]
            kwargs["tool_choice"] = "auto"
        out = llm.create_chat_completion(**kwargs)

        message = (out.get("choices") or [{}])[0].get("message") or {}
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
            return Decision(calls=calls, raw=out)

        text = (message.get("content") or "").strip()

        # Manche Modelle liefern Tool-Calls nur als Text.
        parsed = _parse_text_tool_calls(text)
        if parsed:
            self._pending = parsed
            return Decision(calls=parsed, raw=out)

        if "</think>" in text:  # Qwen3-Thinking-Reste entfernen
            text = re.sub(r"^.*?</think>", "", text, flags=re.S).strip()
        return Decision(calls=[], text=text or None, raw=out)

    async def close(self) -> None:
        llm, self._llm = self._llm, None
        if llm is not None:
            try:
                llm.close()
            except Exception:  # noqa: BLE001
                pass

    def info(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "model": self._spec.key,
            "repo": self._spec.repo,
            "filename": self._spec.filename,
            "loaded": self.loaded,
            "tools": len(self._tools),
        }


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
    if backend in ("llama_cpp", "llama", "llamacpp"):
        spec = llama_models.resolve_model(
            settings.llama_model, settings.llama_repo, settings.llama_filename
        )
        return LlamaCppBackend(
            spec,
            models_dir=str(llama_models.models_dir()),
            n_ctx=settings.llama_n_ctx,
            n_threads=settings.llama_threads,
            n_gpu_layers=settings.llama_gpu_layers,
            temperature=settings.llama_temperature,
            max_tokens=settings.llama_max_tokens,
            disable_thinking=settings.llama_disable_thinking,
            logger=logger,
        )
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
