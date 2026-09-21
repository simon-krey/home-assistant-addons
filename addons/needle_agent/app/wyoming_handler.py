"""Wyoming-``handle``-Handler (Conversation-Agent).

HA schickt ``transcript`` (Text) und erwartet ``handled`` / ``not-handled``.
"""

from __future__ import annotations

import time

from wyoming.asr import Transcript
from wyoming.event import Event
from wyoming.handle import Handled, NotHandled
from wyoming.info import (
    Attribution,
    Describe,
    HandleModel,
    HandleProgram,
    Info,
    SelectProgram,
)
from wyoming.server import AsyncEventHandler

from .state import AppState

PROGRAM_NAME = "needle3"
PROGRAM_VERSION = "0.1.0"


def build_info(state: AppState) -> Info:
    toolset = state.current_toolset()
    languages = [state.settings.language or "de"]
    return Info(
        handle=[
            HandleProgram(
                name=PROGRAM_NAME,
                description="Needle 3 – lokales Tool-Calling für Home Assistant",
                attribution=Attribution(
                    name="Cactus Compute",
                    url="https://github.com/cactus-compute/needle",
                ),
                installed=True,
                version=PROGRAM_VERSION,
                models=[
                    HandleModel(
                        name="needle3",
                        description=f"Needle 3 ({len(toolset)} Tools, {len(toolset.entities)} Entities)",
                        attribution=Attribution(
                            name="Cactus Compute",
                            url="https://huggingface.co/Cactus-Compute/needle3",
                        ),
                        installed=True,
                        languages=languages,
                        version="3",
                    )
                ],
                supports_handled_streaming=False,
                supports_home_control=True,
            )
        ]
    )


class HandleEventHandler(AsyncEventHandler):
    def __init__(self, state: AppState, reader, writer) -> None:
        super().__init__(reader, writer)
        self._state = state

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(build_info(self._state).event())
            return True

        if SelectProgram.is_type(event.type):
            return True

        if Transcript.is_type(event.type):
            transcript = Transcript.from_event(event)
            try:
                result = await self._state.current_engine().process(
                    transcript.text, transcript.language
                )
                with self._state.lock:
                    self._state.stats["requests"] += 1
                    self._state.stats["last_text"] = transcript.text
                    self._state.stats["last_response"] = result.get("response")
                    self._state.stats["last_at"] = time.time()
                await self.write_event(Handled(text=result.get("response") or "").event())
            except Exception as exc:  # noqa: BLE001
                with self._state.lock:
                    self._state.stats["errors"] += 1
                await self.write_event(
                    NotHandled(text=f"Fehler: {type(exc).__name__}").event()
                )
            return True

        return True
