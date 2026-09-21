"""Conversation-Engine: Backend -> Tool-Dispatcher -> Antwort.

Der Ablauf ist backend-unabhaengig:

1. ``backend.begin(text, ...)`` liefert Tool-Calls oder direkt Text.
2. Calls werden ausgefuehrt (HA-Service bzw. Dry-Run) und per ``backend.step``
   zurueckgegeben, bis keine Calls mehr kommen.
3. Gibt es keine Antwort und kein Tool, greift optional Home Assists eigener
   Agent als Fallback.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from .backends import Backend, Decision, HABackend, ToolCall, build_backend
from .entities import EntityInfo, normalize, resolve_entity, resolve_mentions
from .ha_client import HomeAssistantClient
from .responses import build_response
from .settings import Settings
from .tools import ToolSet


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
        self.tool_index_path = str(tool_index_path) if tool_index_path else None
        self.backend: Backend | None = None
        self._build_backend()

    # -- Aufbau ------------------------------------------------------------
    def _system(self, text: str | None = None) -> str:
        backend = (self.settings.backend or "needle").lower()
        if self.settings.system:
            base = self.settings.system
        elif backend in ("llama_cpp", "openai"):
            base = (
                "Du bist ein Home-Assistant-Assistent. Nutze die bereitgestellten "
                "Funktionen, um Geräte zu steuern und Zustände abzufragen. Frage bei "
                "Zustandsfragen immer die passende Funktion ab, statt zu raten. Antworte "
                "kurz und auf Deutsch, ohne Code-Beispiele."
            )
        else:
            base = f"locale: {self.settings.language or 'de'}; device: home assistant"
        if text:
            mentions = resolve_mentions(text, self.toolset.entities)
            if mentions:
                hints = ", ".join(f"'{spoken}' -> {entity.name}" for spoken, entity in mentions)
                base = f"{base}\nErkannte Geräte im Text: {hints}"
        return base

    def _build_backend(self) -> None:
        self.backend = build_backend(
            self.settings,
            self.ha,
            self.toolset.schemas(),
            tool_index_path=self.tool_index_path,
            system=self._system(),
            logger=self.logger,
        )

    def reconfigure(self, settings: Settings, toolset: ToolSet) -> None:
        previous = self.backend
        self.settings = settings
        self.toolset = toolset
        self._build_backend()
        if previous is not None and previous is not self.backend:
            try:
                asyncio.get_running_loop().create_task(previous.close())
            except RuntimeError:
                pass

    # -- Verarbeitung ------------------------------------------------------
    async def process(self, text: str, language: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"response": "", "executed": [], "refusal": True}

        assert self.backend is not None
        started = time.perf_counter()
        executed: list[dict[str, Any]] = []
        error_message: str | None = None
        try:
            decision = await self.backend.begin(text, self._system(text), self.toolset.schemas())
            for _step in range(max(1, self.settings.max_steps)):
                if not decision.calls:
                    break
                if self.settings.ground_calls and not self._grounded(decision.calls, text):
                    self.logger("[GROUNDING] Call verworfen (Geraet nicht im Text genannt)")
                    decision = Decision()
                    break
                results: list[Any] = []
                for call in decision.calls:
                    item = await self._execute(call)
                    executed.append(item)
                    results.append(item["result"])
                decision = await self.backend.step(results)
        except Exception as exc:  # noqa: BLE001
            error_message = f"{type(exc).__name__}: {exc}"
            self.logger(f"[BACKEND ERROR] {error_message}")
            decision = Decision()

        latency_ms = (time.perf_counter() - started) * 1000.0

        response_text = (decision.text or "").strip()
        refusal = False
        low_confidence = False
        error = bool(error_message)
        fallback_used = False

        if not response_text and executed:
            response_text = build_response(
                executed,
                self.settings.templates(),
                dry_run=self.settings.dry_run,
            )
        elif not response_text:
            # Kein Tool, kein Text -> HA-Fallback (falls aktiviert)
            if self.settings.fallback_ha and self.backend.name != "ha":
                try:
                    fallback = HABackend(self.ha, self.settings.language, logger=self.logger)
                    fallback_decision = await fallback.begin(text, self._system(text), [])
                    if fallback_decision.text:
                        response_text = fallback_decision.text
                        fallback_used = True
                        error = False
                except Exception as exc:  # noqa: BLE001
                    self.logger(f"[FALLBACK] HA-Agent nicht erreichbar: {exc}")
            if not response_text:
                refusal = not error
                low_confidence = (
                    not error and decision.confidence is not None and decision.confidence < 0.1
                )
                response_text = build_response(
                    [],
                    self.settings.templates(),
                    refusal=refusal and not low_confidence,
                    low_confidence=low_confidence,
                    error=error,
                )

        result = {
            "response": response_text,
            "needle": decision.raw,
            "executed": executed,
            "refusal": refusal,
            "low_confidence": low_confidence,
            "error": error,
            "error_message": error_message,
            "fallback_ha": fallback_used,
            "backend": self.backend.name,
            "latency_ms": round(latency_ms, 1),
            "language": language or self.settings.language,
            "_text": text,
            "dry_run": self.settings.dry_run and bool(executed),
        }
        self.history.add(result)
        return result

    _ON_EXACT = {"an"}
    _ON_PREFIX = ("einschalt", "anschalt", "anmach", "aktivier")
    _OFF_EXACT = {"aus"}
    _OFF_PREFIX = ("ausschalt", "ausmach", "deaktivier", "abschalt")

    def _polarity_ok(self, call: ToolCall, tokens: list[str]) -> bool:
        """Prueft, ob der Call zur gewuenschten Richtung passt (an/aus).

        Kleine Modelle waehlen bei "aus" manchmal ``turn_on``. Bei Zweifel wird
        der Call verworfen und an den HA-Fallback uebergeben.
        """
        name = call.name.lower()
        if "turn_on" in name or "activate" in name:
            wants = "on"
        elif "turn_off" in name:
            wants = "off"
        else:
            return True

        entity = resolve_entity(self.toolset.index, call.arguments.get("entity_id"))
        if entity is None:
            return True
        entity_tokens = {
            token
            for spoken in entity.spoken_names
            for token in normalize(spoken).split()
            if len(token) >= 4
        }
        positions = [i for i, token in enumerate(tokens) if token in entity_tokens]
        if not positions:
            return True
        center = positions[0]

        best: str | None = None
        best_distance = 99
        for index, token in enumerate(tokens):
            if token in self._ON_EXACT or token.startswith(self._ON_PREFIX):
                polarity = "on"
            elif token in self._OFF_EXACT or token.startswith(self._OFF_PREFIX):
                polarity = "off"
            else:
                continue
            distance = abs(index - center)
            if distance < best_distance:
                best_distance, best = distance, polarity
        if best is not None and best != wants and best_distance <= 4:
            self.logger(f"[POLARITY] {call.name} passt nicht zu '{best}'")
            return False
        return True

    def _grounded(self, calls: list[ToolCall], text: str) -> bool:
        """Prueft, ob die gewaehlten Geraete im Satz vorkommen.

        Verhindert, dass das Modell ein gueltiges, aber falsches Geraet aus dem
        Enum waehlt (z. B. "schreibtisch" -> "Kaffeemaschine"). Nicht gegroundete
        Calls werden verworfen und an den HA-Fallback uebergeben.
        """
        normalized = normalize(text)
        if not normalized:
            return False
        text_tokens = set(normalized.split())
        tokens = normalized.split()
        for call in calls:
            entity_name = call.arguments.get("entity_id")
            if not entity_name:
                return False
            entity = resolve_entity(self.toolset.index, entity_name)
            if entity is None:
                return False
            names = [normalize(name) for name in entity.spoken_names]
            if any(name and name in normalized for name in names):
                pass
            else:
                entity_tokens = {token for name in names for token in name.split() if len(token) >= 4}
                if not (entity_tokens & text_tokens):
                    return False
            if not self._polarity_ok(call, tokens):
                return False
        return True

    async def _execute(self, call: ToolCall) -> dict[str, Any]:
        item: dict[str, Any] = {"name": call.name, "arguments": call.arguments}
        try:
            action = self.toolset.resolve(call.name, call.arguments)
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

        for key in ("percent", "volume", "temperature"):
            if key in call.arguments:
                item["volume"] = call.arguments.get(key)

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
            changed = await self.ha.call_service(
                action.domain, action.service, action.target, action.data
            )
            item["result"] = {"changed_states": changed}
        except Exception as exc:  # noqa: BLE001
            item["result"] = {"error": f"{type(exc).__name__}: {exc}"}
        return item
