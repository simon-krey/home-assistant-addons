"""Event-basierte Pipeline: Audio -> STT -> Needle -> Tool-Dispatcher.

Die Worker kommunizieren ausschliesslich ueber Queues. ``stt`` kennt Needle
nicht und umgekehrt. Nach aussen gehen alle Ereignisse ueber den ``EventBus``.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

import config
from events import EventBus
from homeassistant import StateCache, build_ha_registry, create_backend
from llm.needle import (
    NeedleAgent,
    confidence,
    function_calls,
    is_low_confidence,
    suppressed_calls,
)
from stt.zipformer import StreamingSTT
from tools.registry import ToolRegistry

DEFAULT_SYSTEM = "locale: de-DE; device: pc"
MAX_STEPS = 4


class VoicePipeline:
    def __init__(
        self,
        *,
        debug: bool = False,
        dry_run: bool | None = None,
        bus: EventBus | None = None,
        logger: Callable[[str], None] = print,
        load_stt: bool = True,
    ) -> None:
        self.debug = debug
        self.dry_run = config.HA_DRY_RUN if dry_run is None else dry_run
        self.bus = bus or EventBus()
        self.logger = logger

        self.state_cache = StateCache()
        self.backend = create_backend(
            self.state_cache, on_state_changed=self._on_state_changed, logger=logger
        )
        self.state_cache.update_many(self.backend.get_states())
        self.entities = self.backend.get_entities()

        self.registry: ToolRegistry = build_ha_registry(
            self.backend, self.state_cache, dry_run=lambda: self.dry_run
        )
        if len(self.registry) == 0:
            raise RuntimeError(
                "Keine Tools verfuegbar: Home Assistant lieferte keine passenden Entities."
            )

        self.needle = NeedleAgent(
            self.registry, system=config.NEEDLE_SYSTEM or DEFAULT_SYSTEM
        )
        self.stt: StreamingSTT | None = StreamingSTT() if load_stt else None

        self.audio_queue: "queue.Queue[Any]" = queue.Queue(maxsize=200)
        self.text_queue: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._audio_source: Any = None
        self._audio_thread: threading.Thread | None = None
        self._last_partial = ""
        self._utterance_started_at: float | None = None
        self._first_partial_at: float | None = None

    # -- Lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._spawn(self._needle_worker, "needle")
        if self.stt is not None:
            self._spawn(self._stt_worker, "stt")
        self.publish({"type": "status", "message": "Pipeline gestartet"})

    def stop(self) -> None:
        self._stop.set()
        self.stop_microphone()
        # Queues aufwecken
        try:
            self.audio_queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            self.text_queue.put_nowait("")
        except queue.Full:
            pass
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads.clear()
        try:
            self.backend.close()
        except Exception:  # noqa: BLE001
            pass

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    # -- Mikrofon ----------------------------------------------------------
    def start_microphone(self, device: str | int | None = None) -> None:
        if self._audio_source is not None:
            return
        from audio.microphone import MicrophoneSource

        source = MicrophoneSource(
            sample_rate=config.SAMPLE_RATE,
            channels=config.CHANNELS,
            block_ms=config.AUDIO_BLOCK_MS,
            device=device or config.AUDIO_DEVICE,
        )
        source.start()
        self._audio_source = source
        self._audio_thread = threading.Thread(target=self._audio_worker, name="audio", daemon=True)
        self._audio_thread.start()
        self.publish({"type": "mic", "running": True})

    def stop_microphone(self) -> None:
        if self._audio_source is not None:
            try:
                self._audio_source.stop()
            finally:
                self._audio_source = None
        if self._audio_thread is not None:
            self._audio_thread.join(timeout=2)
            self._audio_thread = None
        self.publish({"type": "mic", "running": False})

    @property
    def microphone_running(self) -> bool:
        return self._audio_source is not None

    # -- Worker ------------------------------------------------------------
    def _audio_worker(self) -> None:
        source = self._audio_source
        if source is None:
            return
        while not self._stop.is_set() and self._audio_source is source:
            chunk = source.read(timeout=0.2)
            if chunk is None:
                continue
            try:
                self.audio_queue.put(chunk, timeout=0.2)
            except queue.Full:
                pass

    def _stt_worker(self) -> None:
        assert self.stt is not None
        while not self._stop.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if chunk is None:
                continue
            try:
                self.stt.accept_audio(chunk)
                text = self.stt.get_result()
            except Exception as exc:  # noqa: BLE001
                self.publish({"type": "error", "source": "stt", "message": str(exc)})
                continue

            if text != self._last_partial:
                self._last_partial = text
                if text:
                    now = time.perf_counter()
                    if self._utterance_started_at is None:
                        self._utterance_started_at = now
                        self._first_partial_at = now
                    self.publish({"type": "stt_partial", "text": text})

            if self.stt.is_endpoint():
                final = (text or "").strip()
                self.stt.reset()
                self._last_partial = ""
                if final:
                    latency = self._final_latency()
                    self.publish({"type": "stt_final", "text": final, "latency_ms": latency})
                    self.text_queue.put(final)
                self._utterance_started_at = None
                self._first_partial_at = None

    def _final_latency(self) -> float | None:
        if self._first_partial_at is None:
            return None
        return (time.perf_counter() - self._first_partial_at) * 1000.0

    def _needle_worker(self) -> None:
        while not self._stop.is_set():
            try:
                text = self.text_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if not text:
                continue
            try:
                self.process_text(text)
            except Exception as exc:  # noqa: BLE001
                self.publish({"type": "error", "source": "needle", "message": str(exc)})

    # -- Kernlogik ---------------------------------------------------------
    def process_text(self, text: str) -> dict[str, Any]:
        """Ein Transkript durch Needle und den Dispatcher schicken."""
        self.publish({"type": "user_text", "text": text})
        needle_start = time.perf_counter()
        response = self.needle.complete(text)
        needle_ms = (time.perf_counter() - needle_start) * 1000.0
        self._publish_needle(response, needle_ms)

        if response.get("success") is False or response.get("error"):
            self.publish(
                {
                    "type": "needle_error",
                    "error": response.get("error"),
                    "error_code": response.get("error_code"),
                }
            )
            self.needle.reset()
            self.publish({"type": "utterance_end"})
            return {"response": response, "results": []}

        calls = function_calls(response)
        if not calls:
            if suppressed_calls(response):
                self.publish(
                    {"type": "needle_suppressed", "calls": suppressed_calls(response)}
                )
            else:
                self.publish({"type": "needle_refusal"})
            self.needle.reset()
            self.publish({"type": "utterance_end"})
            return {"response": response, "results": []}

        if is_low_confidence(response, config.NEEDLE_CONFIDENCE_THRESHOLD):
            self.publish(
                {
                    "type": "low_confidence",
                    "confidence": confidence(response),
                    "threshold": config.NEEDLE_CONFIDENCE_THRESHOLD,
                    "calls": calls,
                }
            )
            self.needle.reset()
            self.publish({"type": "utterance_end"})
            return {"response": response, "results": []}

        all_results: list[Any] = []
        final_response = response
        for _step in range(MAX_STEPS):
            step_results: list[Any] = []
            for call in calls:
                name = call.get("name", "")
                arguments = call.get("arguments") or {}
                self.publish({"type": "tool_call", "name": name, "arguments": arguments})
                result = self.registry.execute(name, arguments)
                self.publish({"type": "tool_result", "name": name, "result": result})
                step_results.append(result)

            all_results.extend(step_results)
            payload: Any = step_results[0] if len(step_results) == 1 else step_results
            start = time.perf_counter()
            final_response = self.needle.feed_result(payload)
            self._publish_needle(final_response, (time.perf_counter() - start) * 1000.0)
            calls = function_calls(final_response)
            if not calls:
                break

        self.publish({"type": "needle_final", "response": final_response})
        self.needle.reset()
        self.publish({"type": "utterance_end"})
        return {"response": final_response, "results": all_results}

    def _publish_needle(self, response: dict[str, Any], latency_ms: float) -> None:
        self.publish(
            {
                "type": "needle",
                "response": response,
                "latency_ms": latency_ms,
                "calls": function_calls(response),
            }
        )

    def _on_state_changed(self, event: dict[str, Any]) -> None:
        data = event.get("data", event)
        self.publish(
            {
                "type": "state_changed",
                "entity_id": data.get("entity_id"),
                "new_state": data.get("new_state"),
            }
        )

    # -- Hilfen ------------------------------------------------------------
    def publish(self, event: dict[str, Any]) -> None:
        self.bus.publish(event)

    def submit_text(self, text: str) -> None:
        text = text.strip()
        if text:
            self.text_queue.put(text)

    def set_dry_run(self, value: bool) -> None:
        self.dry_run = value
        self.publish({"type": "dry_run", "value": value})

    def snapshot(self) -> dict[str, Any]:
        states = self.state_cache.all()
        entities = [
            {
                "entity_id": entity.entity_id,
                "name": entity.name,
                "domain": entity.domain,
                "area": entity.area,
                "state": (states.get(entity.entity_id) or {}).get("state"),
            }
            for entity in self.entities
        ]
        return {
            "backend": type(self.backend).__name__,
            "connected": bool(getattr(self.backend, "connected", False)),
            "dry_run": self.dry_run,
            "tools": self.registry.names(),
            "entities": entities,
            "microphone": self.microphone_running,
            "stt": self.stt is not None,
        }
