"""Wyoming-ASR-Handler.

Ablauf laut Wyoming-Spezifikation:
``describe`` -> ``info`` (mit ASR-Programm + Modell + Sprachen)
``transcribe`` -> ``audio-start`` -> ``audio-chunk``* -> ``audio-stop`` -> ``transcript``
Optional zusaetzlich Streaming: ``transcript-start`` / ``transcript-chunk`` / ``transcript-stop``.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
from wyoming.asr import (
    Transcribe,
    Transcript,
    TranscriptChunk,
    TranscriptStart,
    TranscriptStop,
)
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info, SelectProgram
from wyoming.server import AsyncEventHandler

from .engine import STTEngine
from .state import AppState

PROGRAM_NAME = "sherpa-onnx"
PROGRAM_VERSION = "0.1.0"
TARGET_RATE = 16000


def build_info(engine: STTEngine, streaming: bool, language: str = "") -> Info:
    extra = [part.strip() for part in language.split(",") if part.strip()]
    languages = sorted(set(engine.languages) | set(extra))
    return Info(
        asr=[
            AsrProgram(
                name=PROGRAM_NAME,
                description="Lokales Streaming-STT mit sherpa-onnx (Zipformer)",
                attribution=Attribution(
                    name="k2-fsa / sherpa-onnx",
                    url="https://github.com/k2-fsa/sherpa-onnx",
                ),
                installed=True,
                version=PROGRAM_VERSION,
                models=[
                    AsrModel(
                        name=engine.label,
                        description=f"{engine.label} ({', '.join(languages)})",
                        attribution=Attribution(
                            name="k2-fsa",
                            url="https://github.com/k2-fsa/sherpa-onnx",
                        ),
                        installed=True,
                        languages=languages,
                        version=engine.info()["sherpa_version"],
                    )
                ],
                supports_transcript_streaming=streaming,
                requires_external_vad=True,
            )
        ]
    )


class SttEventHandler(AsyncEventHandler):
    def __init__(self, state: AppState, reader, writer) -> None:
        super().__init__(reader, writer)
        self._state = state
        self._session = None
        self._converter: AudioChunkConverter | None = None
        self._audio = bytearray()
        self._language: str | None = None
        self._last_text = ""
        self._process_seconds = 0.0

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            engine = self._state.current_engine()
            settings = self._state.settings
            info = build_info(engine, settings.streaming_transcripts, settings.language)
            await self.write_event(info.event())
            return True

        if SelectProgram.is_type(event.type):
            return True

        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            self._language = transcribe.language or self._state.settings.language or None
            return True

        if AudioStart.is_type(event.type):
            start = AudioStart.from_event(event)
            self._converter = AudioChunkConverter(rate=TARGET_RATE, width=2, channels=1)
            self._session = self._state.current_engine().create_session()
            self._audio = bytearray()
            self._last_text = ""
            self._process_seconds = 0.0
            if self._state.settings.streaming_transcripts:
                await self.write_event(TranscriptStart(language=self._language).event())
            return True

        if AudioChunk.is_type(event.type):
            chunk = AudioChunk.from_event(event)
            if self._converter is None:
                self._converter = AudioChunkConverter(rate=TARGET_RATE, width=2, channels=1)
            converted = self._converter.convert(chunk)
            if converted.audio and self._session is not None:
                self._audio += converted.audio
                samples = (
                    np.frombuffer(converted.audio, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
                text = await asyncio.to_thread(self._feed, samples)
                if (
                    self._state.settings.streaming_transcripts
                    and text
                    and text != self._last_text
                ):
                    self._last_text = text
                    await self.write_event(TranscriptChunk(text=text).event())
            return True

        if AudioStop.is_type(event.type):
            text = ""
            if self._session is not None:
                text = (await asyncio.to_thread(self._session.finalize) or "").strip()
            if self._state.settings.streaming_transcripts:
                if text and text != self._last_text:
                    await self.write_event(TranscriptChunk(text=text).event())
                await self.write_event(TranscriptStop().event())
            await self.write_event(
                Transcript(text=text, language=self._language).event()
            )
            self._record(text)
            self._session = None
            return True

        return True

    # -- intern ------------------------------------------------------------
    def _feed(self, samples: np.ndarray) -> str:
        if self._session is None:
            return ""
        started = time.perf_counter()
        self._session.accept(samples)
        text = self._session.result()
        self._process_seconds += time.perf_counter() - started
        return text

    def _record(self, text: str) -> None:
        state = self._state
        duration = len(self._audio) / (2 * TARGET_RATE) if self._audio else 0.0
        rtf = self._process_seconds / duration if duration else 0.0
        source = None
        try:
            peer = self.writer.get_extra_info("peername")
            if peer:
                source = f"{peer[0]}:{peer[1]}"
        except Exception:  # noqa: BLE001
            pass

        state.history.add(
            text=text,
            language=self._language,
            audio_pcm=bytes(self._audio),
            rate=TARGET_RATE,
            width=2,
            channels=1,
            duration=duration,
            process_seconds=self._process_seconds,
            rtf=rtf,
            source=source,
        )
        with state.lock:
            state.stats["requests"] += 1
            state.stats["last_text"] = text
            state.stats["last_at"] = time.time()
