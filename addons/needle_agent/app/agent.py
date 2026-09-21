"""Needle-3-Anbindung und Tool-Dispatcher.

Needle liefert ``function_calls``; der Dispatcher uebersetzt sie in
HA-Service-Aufrufe und baut daraus eine Antwort (Needle erzeugt keinen
freien Text).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .entities import EntityInfo
from .ha_client import HomeAssistantClient
from .responses import build_response
from .settings import Settings
from .tools import MAX_DIRECT_TOOLS, ToolSet


class NeedleAgent:
    def __init__(
        self,
        toolset: ToolSet,
        *,
        system: str | None = None,
        max_new_tokens: int = 256,
        tool_index_path: str | Path | None = None,
    ) -> None:
        import needle

        self.toolset = toolset
        self.max_new_tokens = max_new_tokens
        kwargs: dict[str, Any] = {"tools": toolset.schemas()}
        if system:
            kwargs["system"] = system
        if len(toolset) > MAX_DIRECT_TOOLS and tool_index_path:
            path = Path(tool_index_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            kwargs["tool_index_path"] = str(path)
        self.agent = needle.Needle(**kwargs)

    def complete(self, text: str) -> dict[str, Any]:
        return self.agent.complete(text=text, max_new_tokens=self.max_new_tokens)

    def feed_result(self, result: Any) -> dict[str, Any]:
        return self.agent.complete(
            text=json.dumps(result, ensure_ascii=False), max_new_tokens=self.max_new_tokens
        )

    def reset(self) -> None:
        self.agent.reset()


def _calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    return list(response.get("function_calls") or [])


def _suppressed(response: dict[str, Any]) -> list[dict[str, Any]]:
    return list(response.get("suppressed_calls") or [])


def _confidence(response: dict[str, Any]) -> float | None:
    value = response.get("confidence")
    return float(value) if isinstance(value, (int, float)) else None


class ConversationEngine:
    def __init__(
        self,
        settings: Settings,
        ha: HomeAssistantClient,
        toolset: ToolSet,
        history: Any,
        *,
        tool_index_path: str | Path | None = None,
        logger=print,
    ) -> None:
        self.settings = settings
        self.ha = ha
        self.toolset = toolset
        self.history = history
        self.logger = logger
        self.tool_index_path = tool_index_path
        self._build_agent()

    def _system(self) -> str:
        if self.settings.system:
            return self.settings.system
        return f"locale: {self.settings.language or 'de'}; device: home assistant"

    def _build_agent(self) -> None:
        self.agent = NeedleAgent(
            self.toolset,
            system=self._system(),
            tool_index_path=self.tool_index_path,
        )

    def reconfigure(self, settings: Settings, toolset: ToolSet) -> None:
        self.settings = settings
        self.toolset = toolset
        self._build_agent()

    async def process(self, text: str, language: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"response": "", "executed": [], "refusal": True}

        self.agent.reset()
        started = time.perf_counter()
        response = self.agent.complete(text)
        needle_ms = (time.perf_counter() - started) * 1000.0

        if response.get("success") is False or response.get("error"):
            return self._finish(
                text, response, [], needle_ms, error=True, language=language
            )

        calls = _calls(response)
        if not calls:
            suppressed = _suppressed(response)
            return self._finish(
                text,
                response,
                [],
                needle_ms,
                refusal=not suppressed,
                low_confidence=bool(suppressed),
                language=language,
            )

        confidence = _confidence(response)
        threshold = 0.0  # Needle filtert bereits; hier nur optional
        if confidence is not None and confidence < threshold:
            return self._finish(
                text, response, [], needle_ms, low_confidence=True, language=language
            )

        executed: list[dict[str, Any]] = []
        final = response
        for _step in range(max(1, self.settings.max_steps)):
            step_results: list[Any] = []
            for call in calls:
                executed.append(await self._execute(call))
                step_results.append(executed[-1]["result"])
            payload: Any = step_results[0] if len(step_results) == 1 else step_results
            final = self.agent.feed_result(payload)
            calls = _calls(final)
            if not calls:
                break

        return self._finish(text, final, executed, needle_ms, language=language)

    async def _execute(self, call: dict[str, Any]) -> dict[str, Any]:
        name = call.get("name", "")
        arguments = call.get("arguments") or {}
        item: dict[str, Any] = {"name": name, "arguments": arguments}
        try:
            action = self.toolset.resolve(name, arguments)
        except ValueError as exc:
            item["result"] = {"error": str(exc)}
            return item

        entity: EntityInfo | None = self.toolset.by_id.get(action.target.get("entity_id", ""))
        item["entity_name"] = entity.name if entity else action.target.get("entity_id")
        item["entity_id"] = action.target.get("entity_id")

        if action.read_state:
            state = self.ha.state(action.target["entity_id"]) or {}
            item["state"] = state.get("state")
            item["result"] = {
                "entity_id": action.target["entity_id"],
                "state": state.get("state"),
                "attributes": state.get("attributes", {}),
            }
            return item

        if "percent" in arguments:
            item["volume"] = arguments.get("percent")
        if "volume" in arguments:
            item["volume"] = arguments.get("volume")
        if "temperature" in arguments:
            item["volume"] = arguments.get("temperature")

        if self.settings.dry_run:
            item["result"] = {
                "dry_run": True,
                "domain": action.domain,
                "service": action.service,
                "target": action.target,
                "data": action.data,
            }
            return item

        try:
            changed = await self.ha.call_service(action.domain, action.service, action.target, action.data)
            item["result"] = {"changed_states": changed}
        except Exception as exc:  # noqa: BLE001
            item["result"] = {"error": f"{type(exc).__name__}: {exc}"}
        return item

    def _finish(
        self,
        text: str,
        response: dict[str, Any],
        executed: list[dict[str, Any]],
        needle_ms: float,
        *,
        refusal: bool = False,
        low_confidence: bool = False,
        error: bool = False,
        language: str | None = None,
    ) -> dict[str, Any]:
        response_text = build_response(
            executed,
            self.settings.templates(),
            dry_run=self.settings.dry_run and bool(executed),
            refusal=refusal,
            low_confidence=low_confidence,
            error=error,
        )
        result = {
            "response": response_text,
            "needle": response,
            "executed": executed,
            "refusal": refusal,
            "low_confidence": low_confidence,
            "error": error,
            "needle_ms": round(needle_ms, 1),
            "language": language or self.settings.language,
            "_text": text,
            "dry_run": self.settings.dry_run and bool(executed),
        }
        self.history.add(result)
        return result
