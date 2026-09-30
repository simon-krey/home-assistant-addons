"""Laya als Entscheidungsschicht (System 1).

Laya ist ein nicht-autoregressives Entscheidungsmodell (Apache-2.0,
ModernBERT/mmBERT, ~322–421M): Man gibt einen Zustand (Text) und typisierte
Fragen (`choice`, `score`, `noul`) und erhaelt Antworten mit kalibrierten
Wahrscheinlichkeiten – **ohne** Textgenerierung. Es ruft selbst keine Tools
auf; wir nutzen es, um Aktion und Geraet zu waehlen.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

LAYA_MODELS: dict[str, str] = {
    "multilingual": "convaiinnovations/laya-multilingual",
    "english": "convaiinnovations/laya",
}
DEFAULT_LAYA_MODEL = "multilingual"


class LayaUnavailable(RuntimeError):
    pass


class LayaDecision:
    def __init__(
        self,
        model_key: str = DEFAULT_LAYA_MODEL,
        *,
        preload: bool = True,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.model_key = model_key if model_key in LAYA_MODELS else DEFAULT_LAYA_MODEL
        self.model_id = LAYA_MODELS[self.model_key]
        self.logger = logger
        self._agent: Any = None
        self._lock = threading.Lock()
        if preload:
            self.load()

    def load(self) -> Any:
        with self._lock:
            if self._agent is not None:
                return self._agent
            try:
                import laya  # type: ignore
            except Exception as exc:  # noqa: BLE001
                raise LayaUnavailable(f"laya ist nicht installiert: {exc}") from exc
            self.logger(f"[LAYA] Lade {self.model_id} ...")
            started = time.perf_counter()
            self._agent = laya.load(self.model_id)
            self.logger(f"[LAYA] Modell geladen in {time.perf_counter() - started:.1f}s")
            return self._agent

    @property
    def loaded(self) -> bool:
        return self._agent is not None

    def predict(self, text: str, questions: dict[str, Any]) -> dict[str, Any]:
        agent = self.load()
        state = {"text": text}
        try:
            return agent.predict(state, questions)
        except Exception:  # noqa: BLE001 - manche Versionen wollen den reinen String
            return agent.predict(text, questions)

    def close(self) -> None:
        """Modell freigeben (Speicher)."""
        with self._lock:
            self._agent = None

    def info(self) -> dict[str, Any]:
        return {"model": self.model_key, "model_id": self.model_id, "loaded": self.loaded}


_CACHE: dict[str, LayaDecision] = {}
_CACHE_LOCK = threading.Lock()


def get_laya(
    model_key: str = DEFAULT_LAYA_MODEL,
    *,
    preload: bool = True,
    logger: Callable[[str], None] = print,
) -> LayaDecision:
    """Laya-Modell pro Modell-Key einmal laden (ueberlebt Agent-Rebuilds)."""
    key = model_key if model_key in LAYA_MODELS else DEFAULT_LAYA_MODEL
    with _CACHE_LOCK:
        if key not in _CACHE:
            _CACHE[key] = LayaDecision(key, preload=preload, logger=logger)
        return _CACHE[key]


def choice_of(result: dict[str, Any], key: str) -> tuple[str | None, float | None]:
    """``choice`` + Confidence robust aus der Laya-Antwort ziehen."""
    answers = (result or {}).get("answers") or {}
    entry = answers.get(key)
    if not isinstance(entry, dict):
        return None, None
    choice = entry.get("choice")
    # Laya liefert "answer_confidence" (Wahrscheinlichkeit der gewaehlten Option)
    # und "confidence" (kalibrierte Act-Confidence) – wir nehmen ersteres.
    confidence = entry.get("answer_confidence", entry.get("confidence"))
    if confidence is None:
        for field in ("probabilities", "scores", "options"):
            values = entry.get(field)
            if isinstance(values, dict) and choice in values:
                try:
                    confidence = float(values[choice])
                except (TypeError, ValueError):
                    confidence = None
                break
    return (str(choice) if choice is not None else None,
            float(confidence) if isinstance(confidence, (int, float)) else None)
