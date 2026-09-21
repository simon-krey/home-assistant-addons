"""Tool-Registry.

Haelt Tool-Schemas (JSON-Schema, wie Needle sie erwartet) und die zugehoerigen
Handler. Die Schemas werden dynamisch erzeugt, damit z. B. die erlaubten
Entity-Namen als ``enum`` im Decode-Grammar landen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler | None = None

    def schema(self) -> dict[str, Any]:
        """Needle-kompatibles Schema (Name, Beschreibung, Parameter)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool bereits registriert: {spec.name}")
        self._specs[spec.name] = spec

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    def names(self) -> list[str]:
        return list(self._specs)

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self._specs.values()]

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def execute(self, name: str, arguments: dict[str, Any] | None) -> Any:
        """Handler ausfuehren und Fehler als Ergebnisobjekt zurueckgeben."""
        spec = self._specs.get(name)
        if spec is None:
            return {"error": f"Unbekanntes Tool: {name}"}
        if spec.handler is None:
            return {"error": f"Tool {name} hat keinen Handler"}
        try:
            return spec.handler(dict(arguments or {}))
        except Exception as exc:  # noqa: BLE001 - soll die Pipeline nicht beenden
            return {"error": f"{type(exc).__name__}: {exc}"}
