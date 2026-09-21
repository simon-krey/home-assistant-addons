"""Conversation-Engine: Fast-Path -> Needle -> Tool-Dispatcher -> Antwort.

Reihenfolge:

1. **Fast-Path** (`commands.CommandParser`): eindeutige deutsche Kommandos
   werden ohne Modell ausgefuehrt – schnell und reproduzierbar.
2. **Needle**: alles andere. Der Text wird vorher um einen Geraetehinweis
   ergaenzt (Fuzzy-/Phonetik-Aufloesung), damit Needle den kanonischen Namen
   sieht.
3. **HA-Fallback**: wenn Needle nichts Brauchbares liefert.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any

from .backends import Backend, Decision, HABackend, ToolCall, build_backend
from .commands import Command, CommandParser, action_to_service
from .entities import EntityInfo
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
        force_backend: str | None = None,
        logger=print,
    ) -> None:
        self.settings = settings
        self.ha = ha
        self.toolset = toolset
        self.history = history
        self.logger = logger
        self.tool_index_path = str(tool_index_path) if tool_index_path else None
        self.force_backend = force_backend
        self.resolver = toolset.resolver
        self.parser = CommandParser(self.resolver)
        self.backend: Backend | None = None
        self._build_backend()

    # -- Aufbau ------------------------------------------------------------
    def _system(self, text: str | None = None) -> str:
        base = self.settings.system or f"locale: {self.settings.language or 'de'}; device: home assistant"
        if text:
            mentions = self.resolver.mentions(text)
            if mentions:
                hints = ", ".join(f"'{c.entity.name}'" for c in mentions[:4])
                base = f"{base}\nErkannte Geräte im Text: {hints}"
        return base

    def _build_backend(self) -> None:
        settings = self.settings
        if self.force_backend:
            settings = replace(self.settings, backend=self.force_backend)
        self.backend = build_backend(
            settings,
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
        self.resolver = toolset.resolver
        self.parser = CommandParser(self.resolver)
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

        started = time.perf_counter()

        # 1) Fast-Path
        command = self.parser.parse(text) if self.settings.fast_path else None
        if command is not None:
            executed = [await self._execute_command(command)]
            response_text = build_response(
                executed, self.settings.templates(), dry_run=self.settings.dry_run
            )
            result = {
                "response": response_text,
                "needle": None,
                "executed": executed,
                "refusal": False,
                "low_confidence": False,
                "error": False,
                "error_message": None,
                "error_traceback": None,
                "fallback_ha": False,
                "backend": "rule",
                "source": "rule",
                "command": {
                    "action": command.action,
                    "entity": command.entity.entity_id,
                    "value": command.value,
                    "reason": command.reason,
                },
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                "language": language or self.settings.language,
                "_text": text,
                "dry_run": self.settings.dry_run,
            }
            self.history.add(result)
            return result

        # 2) Needle (mit Geraetehinweis)
        assert self.backend is not None
        annotated, candidate = self.resolver.annotate(text)
        if candidate is not None:
            self.logger(
                f"[RESOLVER] '{text}' -> {candidate.entity.name} ({candidate.score}, {candidate.reason})"
            )

        executed: list[dict[str, Any]] = []
        error_message: str | None = None
        error_traceback: str | None = None
        try:
            decision = await self.backend.begin(
                annotated, self._system(text), self.toolset.schemas()
            )
            for _step in range(max(1, self.settings.max_steps)):
                if not decision.calls:
                    break
                if self.settings.ground_calls and not self._grounded(decision.calls, text):
                    self.logger("[GROUNDING] Call verworfen (Geraet/Polaritaet passt nicht)")
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
            error_traceback = traceback.format_exc()
            self.logger(f"[BACKEND ERROR] {error_message}\n{error_traceback}")
            decision = Decision()

        latency_ms = (time.perf_counter() - started) * 1000.0
        response_text = (decision.text or "").strip()
        refusal = False
        low_confidence = False
        error = bool(error_message)
        fallback_used = False

        if not response_text and executed:
            response_text = build_response(
                executed, self.settings.templates(), dry_run=self.settings.dry_run
            )
        elif not response_text:
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
                if error and self.settings.debug_errors and error_message:
                    response_text = f"{response_text} ({self.backend.name}: {error_message})"

        result = {
            "response": response_text,
            "needle": decision.raw,
            "executed": executed,
            "refusal": refusal,
            "low_confidence": low_confidence,
            "error": error,
            "error_message": error_message,
            "error_traceback": (error_traceback[-2000:] if error_traceback else None),
            "fallback_ha": fallback_used,
            "backend": self.backend.name,
            "source": "needle",
            "resolved": (
                {"entity": candidate.entity.entity_id, "score": candidate.score, "reason": candidate.reason}
                if candidate
                else None
            ),
            "annotated": annotated if annotated != text else None,
            "latency_ms": round(latency_ms, 1),
            "language": language or self.settings.language,
            "_text": text,
            "dry_run": self.settings.dry_run and bool(executed),
        }
        self.history.add(result)
        return result

    # -- Ausfuehrung -------------------------------------------------------
    async def _execute_command(self, command: Command) -> dict[str, Any]:
        entity = command.entity
        if command.action in ("volume_up", "volume_down"):
            state = self.ha.state(entity.entity_id) or {}
            current = float((state.get("attributes") or {}).get("volume_level") or 0.5)
            step = 0.1 if command.action == "volume_up" else -0.1
            command.value = max(0.0, min(1.0, round(current + step, 2)))
        domain, service, data = action_to_service(command)
        item: dict[str, Any] = {
            "name": command.action,
            "arguments": {"entity_id": entity.name},
            "entity_name": entity.name,
            "entity_id": entity.entity_id,
            "reason": command.reason,
        }
        if command.value is not None:
            item["volume"] = command.value
            item["arguments"]["value"] = command.value

        if command.action == "get_state":
            state = self.ha.state(entity.entity_id) or {}
            item["state"] = state.get("state")
            item["result"] = {
                "entity_id": entity.entity_id,
                "state": state.get("state"),
                "attributes": state.get("attributes", {}),
            }
            return item

        if self.settings.dry_run:
            item["result"] = {
                "dry_run": True,
                "domain": domain,
                "service": service,
                "target": {"entity_id": entity.entity_id},
                "data": data,
            }
            return item
        try:
            changed = await self.ha.call_service(domain, service, {"entity_id": entity.entity_id}, data)
            item["result"] = {"changed_states": changed}
        except Exception as exc:  # noqa: BLE001
            item["result"] = {"error": f"{type(exc).__name__}: {exc}"}
        return item

    def _polarity_ok(self, call: ToolCall, tokens: list[str]) -> bool:
        name = call.name.lower()
        if "turn_on" in name or "activate" in name:
            wants = "on"
        elif "turn_off" in name:
            wants = "off"
        else:
            return True

        candidate = self.resolver.best(call.arguments.get("entity_id") or "")
        if candidate is None:
            return True
        entity = candidate.entity
        entity_tokens = {
            token
            for spoken in entity.spoken_names
            for token in _normalize(spoken).split()
            if len(token) >= 4
        }
        positions = [i for i, token in enumerate(tokens) if token in entity_tokens]
        if not positions:
            return True
        center = positions[0]

        best: str | None = None
        best_distance = 99
        for index, token in enumerate(tokens):
            if token in _ON_EXACT or token.startswith(_ON_PREFIX):
                polarity = "on"
            elif token in _OFF_EXACT or token.startswith(_OFF_PREFIX):
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
        normalized = _normalize(text)
        if not normalized:
            return False
        text_tokens = set(normalized.split())
        tokens = normalized.split()
        for call in calls:
            entity_name = call.arguments.get("entity_id")
            if not entity_name:
                return False
            candidate = self.resolver.best(entity_name, min_score=0.6, min_margin=0.0)
            if candidate is None:
                return False
            entity = candidate.entity
            names = [_normalize(name) for name in entity.spoken_names]
            if not any(name and name in normalized for name in names):
                entity_tokens = {t for name in names for t in name.split() if len(t) >= 4}
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


_ON_EXACT = {"an"}
_ON_PREFIX = ("einschalt", "anschalt", "anmach", "aktivier")
_OFF_EXACT = {"aus"}
_OFF_PREFIX = ("ausschalt", "ausmach", "deaktivier", "abschalt")


def _normalize(text: str) -> str:
    from .entities import normalize

    return normalize(text)
