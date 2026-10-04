"""Backends.

Es gibt nur noch **Needle 3** als Modell. ``HABackend`` bleibt als
Sicherheitsnetz (Fallback), wenn Needle nichts Brauchbares liefert – es ist
kein eigenes Modell, sondern Home Assists eigener Agent.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .commands import DOMAIN_FOR_ACTION, detect_action
from .ha_client import HomeAssistantClient
from .laya_decision import LayaDecision, LayaUnavailable, choice_of, get_laya
from .settings import Settings

if TYPE_CHECKING:
    from .tools import ToolSet

MAX_DIRECT_TOOLS = 5

BACKENDS = ("needle", "laya", "openai")

# Aktionen, die der Dispatcher auch ohne passendes Tool ausfuehren kann
GENERIC_ACTIONS = (
    "turn_on",
    "turn_off",
    "set_brightness",
    "get_state",
    "set_volume",
    "set_temperature",
    "volume_up",
    "volume_down",
)

ACTION_OPTIONS: dict[str, str] = {
    "turn_on": "ein Gerät einschalten (Licht, Lampe, Schalter, Szene)",
    "turn_off": "ein Gerät ausschalten (Licht, Lampe, Schalter)",
    "set_brightness": "die Helligkeit eines Lichts in Prozent setzen",
    "get_state": "den Zustand eines Geräts abfragen",
    "set_volume": "die Lautstärke eines Mediaplayers setzen",
    "set_temperature": "die Temperatur einer Heizung setzen",
    "none": "keine Geräteaktion (z. B. Smalltalk oder unbekannt)",
}


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


# ---------------------------------------------------------------------------
# Laya (System-1-Entscheidungsmodell)
# ---------------------------------------------------------------------------
class LayaBackend:
    """Laya als Entscheidungsschicht.

    **Aktion** kommt aus den zuverlaessigen Regel-Schluesselwoertern (Laya
    klassifiziert deutsche Ein/Aus-Polaritaet unzuverlaessig). Nur wenn die
    Regeln nichts finden, fragt Laya. **Geraet** waehlt Laya kontextbewusst
    aus den Kandidaten des Resolvers – das ist seine Staerke.
    """

    name = "laya"

    def __init__(
        self,
        settings: Settings,
        ha: HomeAssistantClient,
        toolset: "ToolSet",
        *,
        logger=print,
    ) -> None:
        self.settings = settings
        self.ha = ha
        self.toolset = toolset
        self.resolver = toolset.resolver
        self.logger = logger
        self._laya = get_laya(
            settings.laya_model,
            preload=settings.laya_preload,
            logger=logger,
        )

    def reset(self) -> None:
        return None

    async def begin(self, text: str, system: str, tools: list[dict[str, Any]]) -> Decision:
        self._laya.load()  # wirft LayaUnavailable, wenn nicht installiert

        threshold = self.settings.laya_confidence_threshold
        action = detect_action(text)
        confidence: float | None = None
        if action is None:
            action, confidence = await self._ask_action(text)
        if not action or action == "none":
            return Decision(calls=[], confidence=confidence)
        if confidence is not None and confidence < threshold:
            self.logger(f"[LAYA] Aktion '{action}' unter Schwelle ({confidence} < {threshold})")
            return Decision(calls=[], confidence=confidence)

        domain = DOMAIN_FOR_ACTION.get(action)
        value = self._value_for(action, text)
        limit = max(1, self.settings.laya_max_candidates)
        candidates = self.resolver.resolve(text, domain=domain)[:limit]
        if not candidates:
            return Decision(calls=[], confidence=confidence)

        entity = candidates[0].entity
        if len(candidates) > 1:
            picked, device_confidence = await self._ask_device(text, candidates)
            if not picked or picked == "keins":
                return Decision(calls=[], confidence=device_confidence or confidence)
            if device_confidence is not None and device_confidence < threshold:
                self.logger(f"[LAYA] Geraet unter Schwelle ({device_confidence} < {threshold})")
                return Decision(calls=[], confidence=device_confidence)
            entity = next((c.entity for c in candidates if c.entity.name == picked), entity)
            if device_confidence is not None:
                confidence = (
                    device_confidence if confidence is None else min(confidence, device_confidence)
                )

        arguments: dict[str, Any] = {"entity_id": entity.name}
        if value is not None:
            arguments["value"] = value
        self.logger(f"[LAYA] {action} -> {entity.name} (confidence={confidence})")
        return Decision(calls=[ToolCall(action, arguments)], confidence=confidence)

    async def step(self, results: list[Any]) -> Decision:
        return Decision(calls=[])

    async def close(self) -> None:
        return None

    def info(self) -> dict[str, Any]:
        return {"backend": self.name, **self._laya.info(), "tools": len(self.toolset)}

    async def _ask_action(self, text: str) -> tuple[str | None, float | None]:
        questions = {
            "action": {
                "type": "choice",
                "instructions": "Welche Aktion beschreibt der Nutzer?",
                "criteria": ACTION_OPTIONS,
            }
        }
        result = await asyncio.to_thread(self._laya.predict, text, questions)
        return choice_of(result, "action")

    async def _ask_device(
        self, text: str, candidates: list[Any]
    ) -> tuple[str | None, float | None]:
        criteria = {c.entity.name: (c.entity.area or "Gerät") for c in candidates}
        criteria["keins"] = "keines dieser Geräte ist gemeint"
        questions = {
            "device": {
                "type": "choice",
                "instructions": "Welches Gerät ist mit dem Satz gemeint?",
                "criteria": criteria,
            }
        }
        result = await asyncio.to_thread(self._laya.predict, text, questions)
        return choice_of(result, "device")

    @staticmethod
    def _value_for(action: str, text: str) -> float | None:
        if action not in ("set_brightness", "set_volume", "set_temperature"):
            return None
        match = re.search(r"(\d{1,3})(?:[.,](\d+))?", text)
        if not match:
            return None
        value = float(f"{match.group(1)}.{match.group(2)}" if match.group(2) else match.group(1))
        if action == "set_volume" and value > 1:
            value = value / 100.0
        return value


def _openai_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool-Schemas in das OpenAI-Format bringen."""
    tools: list[dict[str, Any]] = []
    for schema in schemas:
        name = schema.get("name")
        if not name:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": schema.get("description") or "",
                    "parameters": schema.get("parameters")
                    or {"type": "object", "properties": {}},
                },
            }
        )
    return tools


class OpenAIBackend:
    """Beliebiges **OpenAI-kompatibles** Chat-API mit Tool-Calling.

    Funktioniert mit OpenAI selbst, aber auch mit kompatiblen Endpunkten wie
    Ollama (``http://localhost:11434/v1``), LM Studio
    (``http://localhost:1234/v1``), OpenRouter, vLLM usw. Der API-Key ist
    optional (lokale Server brauchen oft keinen).
    """

    name = "openai"

    def __init__(
        self,
        settings: Settings,
        tool_schemas: list[dict[str, Any]],
        *,
        system: str = "",
        logger=print,
    ) -> None:
        import httpx

        self.settings = settings
        self.logger = logger
        self._tools = _openai_tools(tool_schemas)
        self._system = system
        self._messages: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = []
        self._assistant_message: dict[str, Any] | None = None
        base = (settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"Content-Type": "application/json"}
        if settings.openai_api_key:
            headers["Authorization"] = f"Bearer {settings.openai_api_key}"
        self._client = httpx.AsyncClient(
            base_url=base,
            headers=headers,
            timeout=float(settings.openai_timeout or 30),
        )

    def reset(self) -> None:
        self._messages = []
        self._pending = []
        self._assistant_message = None

    async def begin(self, text: str, system: str, tools: list[dict[str, Any]]) -> Decision:
        self.reset()
        if tools:
            self._tools = _openai_tools(tools)
        self._messages = [
            {"role": "system", "content": system or self._system or ""},
            {"role": "user", "content": text},
        ]
        return await self._complete()

    async def step(self, results: list[Any]) -> Decision:
        if self._assistant_message is not None:
            self._messages.append(self._assistant_message)
        for index, (call, result) in enumerate(zip(self._pending, results)):
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{index}",
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
        return await self._complete()

    async def _complete(self) -> Decision:
        payload: dict[str, Any] = {
            "model": self.settings.openai_model or "gpt-4o-mini",
            "messages": self._messages,
            "temperature": float(self.settings.openai_temperature or 0.0),
            "max_tokens": int(self.settings.openai_max_tokens or 256),
        }
        if self._tools:
            payload["tools"] = self._tools
            payload["tool_choice"] = "auto"
        response = await self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        message = ((data.get("choices") or [{}])[0].get("message")) or {}

        # tool_calls normalisieren (manche Server liefern keine id)
        pending: list[dict[str, Any]] = []
        calls: list[ToolCall] = []
        for index, item in enumerate(message.get("tool_calls") or []):
            if not isinstance(item, dict):
                continue
            if not item.get("id"):
                item["id"] = f"call_{index}"
            item.setdefault("type", "function")
            function = item.get("function") or {}
            raw = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            pending.append(item)
            calls.append(ToolCall(str(function.get("name") or ""), arguments, item.get("id")))

        self._assistant_message = message
        self._pending = pending

        content = message.get("content")
        if isinstance(content, list):  # manche APIs liefern Content-Bloecke
            content = " ".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            )
        text = (str(content).strip() or None) if content else None
        if calls:
            text = None
        if pending:
            self.logger(
                "[OPENAI] " + ", ".join(f"{c.name}({c.arguments})" for c in calls)
            )
        return Decision(calls=calls, text=text, confidence=None, raw=data)

    async def close(self) -> None:
        await self._client.aclose()

    def info(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "model": self.settings.openai_model,
            "base_url": self.settings.openai_base_url,
            "tools": len(self._tools),
            "api_key": bool(self.settings.openai_api_key),
        }


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
    toolset: "ToolSet | None" = None,
    tool_index_path: str | None = None,
    system: str,
    logger=print,
) -> Backend:
    backend = (settings.backend or "needle").lower()
    if backend == "laya" and toolset is not None:
        return LayaBackend(settings, ha, toolset, logger=logger)
    if backend == "openai":
        return OpenAIBackend(settings, tool_schemas, system=system, logger=logger)
    if backend == "ha":
        return HABackend(ha, settings.language, logger=logger)
    if not tool_schemas:
        logger("[BACKEND] Keine Tools verfuegbar – nutze Home-Assistant-Agent")
        return HABackend(ha, settings.language, logger=logger)
    return NeedleBackend(
        tool_schemas,
        system=system,
        tool_index_path=tool_index_path,
        max_new_tokens=settings.needle_max_tokens,
    )
