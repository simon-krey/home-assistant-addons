"""sherpa-onnx STT fuer den Wyoming-Server.

Zwei Engine-Familien:

* **Streaming** (``OnlineRecognizer.from_transducer``): Zipformer-Transducer
  mit ``encoder``/``decoder``/``joiner``/``tokens``. Liefert Partials.
* **Offline** (``OfflineRecognizer``): NVIDIA Parakeet TDT v3
  (``from_transducer``, ``model_type="nemo_transducer"``). Dekodiert erst nach
  ``audio-stop``.

Beide stellen dieselbe Session-Schnittstelle bereit (``accept``/``result``/
``finalize``), damit der Wyoming-Handler identisch bleibt.
"""

from __future__ import annotations

import io
import os
import tarfile
import threading
import time
import urllib.request
import wave
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import numpy as np
import sherpa_onnx

LogFn = Callable[[str], None]

_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"

PARAKEET_LANGS = (
    "en", "de", "fr", "es", "it", "nl", "pt", "pl", "ru", "uk", "cs", "sv",
    "da", "fi", "no", "hu", "ro", "el", "tr", "bg", "hr", "sk", "sl", "et",
    "lv", "lt",
)

# Nemotron 3.5: 19 "transcription-ready" + 13 "broad-coverage" Locales.
NEMOTRON_LANGS = (
    "en", "es", "fr", "it", "pt", "nl", "de", "tr", "ru", "ar", "hi", "ja",
    "ko", "vi", "uk", "pl", "sv", "cs", "nb", "da", "bg", "fi", "hr", "sk",
    "zh", "hu", "ro", "et",
)

# Whisper (multilingual) – Auswahl der wichtigsten Sprachen.
WHISPER_LANGS = (
    "de", "en", "es", "fr", "it", "nl", "pt", "pl", "ru", "tr", "uk", "cs",
    "sv", "da", "fi", "no", "hu", "ro", "el", "ar", "zh", "ja", "ko",
)


def log(message: str) -> None:
    print(message, flush=True)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    url: str
    languages: tuple[str, ...]
    kind: str = "streaming"  # streaming | parakeet | nemotron
    model_type: str = ""
    dir: str = ""
    language: str = ""  # Standard-Quellsprache fuer Offline-Modelle
    feature_dim: int = 80
    language_option: bool = False  # Sprache pro Stream setzen (Nemotron)


def _url(name: str) -> str:
    return _RELEASE + name


# Kroko-Streaming-Modelle + NVIDIA Parakeet TDT v3 (offline) + Nemotron 3.5.
MODELS: dict[str, ModelSpec] = {
    "de": ModelSpec(
        "de", "Deutsch – Kroko (Streaming)",
        _url("sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06.tar.bz2"), ("de",),
    ),
    "en-kroko": ModelSpec(
        "en-kroko", "English – Kroko (Streaming)",
        _url("sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06.tar.bz2"), ("en",),
    ),
    "es-kroko": ModelSpec(
        "es-kroko", "Español – Kroko (Streaming)",
        _url("sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06.tar.bz2"), ("es",),
    ),
    "fr-kroko": ModelSpec(
        "fr-kroko", "Français – Kroko (Streaming)",
        _url("sherpa-onnx-streaming-zipformer-fr-kroko-2025-08-06.tar.bz2"), ("fr",),
    ),
    "parakeet-v3": ModelSpec(
        "parakeet-v3", "NVIDIA Parakeet TDT 0.6B v3 (Offline, 25 EU-Sprachen)",
        _url("sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"),
        PARAKEET_LANGS, kind="parakeet", language="de",
    ),
    "nemotron-v3": ModelSpec(
        "nemotron-v3", "NVIDIA Nemotron 3.5 ASR Streaming (560 ms, Streaming)",
        _url("sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2"),
        NEMOTRON_LANGS, kind="nemotron", model_type="nemotron",
        feature_dim=128, language_option=True,
    ),
    "nemotron-v3-hq": ModelSpec(
        "nemotron-v3-hq", "NVIDIA Nemotron 3.5 ASR Streaming (1120 ms, genauer, Streaming)",
        _url("sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-1120ms-int8-2026-06-11.tar.bz2"),
        NEMOTRON_LANGS, kind="nemotron", model_type="nemotron",
        feature_dim=128, language_option=True,
    ),
    # --- Whisper (Offline, multilingual; edge-optimiert via sherpa-onnx int8) ---
    "whisper-tiny": ModelSpec(
        "whisper-tiny", "Whisper tiny (Offline, ~39M)",
        _url("sherpa-onnx-whisper-tiny.tar.bz2"), WHISPER_LANGS, kind="whisper", language="de",
    ),
    "whisper-tiny.en": ModelSpec(
        "whisper-tiny.en", "Whisper tiny.en (Offline, nur Englisch, ~39M)",
        _url("sherpa-onnx-whisper-tiny.en.tar.bz2"), ("en",), kind="whisper", language="en",
    ),
    "whisper-base": ModelSpec(
        "whisper-base", "Whisper base (Offline, ~74M)",
        _url("sherpa-onnx-whisper-base.tar.bz2"), WHISPER_LANGS, kind="whisper", language="de",
    ),
    "whisper-small": ModelSpec(
        "whisper-small", "Whisper small (Offline, ~244M)",
        _url("sherpa-onnx-whisper-small.tar.bz2"), WHISPER_LANGS, kind="whisper", language="de",
    ),
    "whisper-medium": ModelSpec(
        "whisper-medium", "Whisper medium (Offline, ~769M, Pi 5 langsam)",
        _url("sherpa-onnx-whisper-medium.tar.bz2"), WHISPER_LANGS, kind="whisper", language="de",
    ),
    "whisper-large-v3": ModelSpec(
        "whisper-large-v3", "Whisper large-v3 (Offline, ~1,5G, nur x86/GPU sinnvoll)",
        _url("sherpa-onnx-whisper-large-v3.tar.bz2"), WHISPER_LANGS, kind="whisper", language="de",
    ),
    "whisper-turbo": ModelSpec(
        "whisper-turbo", "Whisper turbo (Offline, ~809M, schnell + genau)",
        _url("sherpa-onnx-whisper-turbo.tar.bz2"), WHISPER_LANGS, kind="whisper", language="de",
    ),
    "custom": ModelSpec("custom", "Eigene Modell-URL (siehe model_url)", "", ("de",)),
}


@dataclass(frozen=True)
class ModelFiles:
    encoder: Path
    decoder: Path
    tokens: Path
    joiner: Path | None = None


def default_models_dir() -> Path:
    env = os.getenv("MODELS_DIR")
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data/models")
    return Path(__file__).resolve().parent.parent / "models"


def archive_stem(url: str) -> str:
    name = Path(url).name
    for suffix in (".tar.bz2", ".tar.gz", ".tgz", ".tar", ".zip"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def resolve_spec(
    model_key: str,
    model_url: str | None = None,
    model_type: str | None = None,
    kind: str | None = None,
) -> ModelSpec:
    """Preset aufloesen; URL/Typ/Art duerfen ueberschreiben."""
    spec = MODELS.get(model_key, MODELS["de"])
    resolved_kind = kind or spec.kind
    resolved_type = model_type if model_type is not None else spec.model_type
    if not model_url:
        if resolved_kind == spec.kind and resolved_type == spec.model_type:
            return spec
        return replace(spec, kind=resolved_kind, model_type=resolved_type)
    return replace(
        spec,
        key=spec.key if model_key in MODELS else "custom",
        url=model_url,
        kind=resolved_kind,
        model_type=resolved_type,
        dir=archive_stem(model_url),
    )


def _download(url: str, dest: Path, logger: LogFn = log) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    logger(f"[MODEL] Download: {url}")
    with urllib.request.urlopen(url, timeout=60) as response, open(tmp, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        last = time.time()
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if time.time() - last > 5:
                last = time.time()
                logger(
                    f"[MODEL]   {done / 1e6:.1f}/{total / 1e6:.1f} MB"
                    if total
                    else f"[MODEL]   {done / 1e6:.1f} MB"
                )
    tmp.rename(dest)


def ensure_model(spec: ModelSpec, models_dir: str | Path | None = None, logger: LogFn = log) -> Path:
    if not spec.url:
        raise ValueError("Keine Modell-URL gesetzt")
    directory = Path(models_dir) if models_dir else default_models_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (spec.dir or archive_stem(spec.url))
    if _looks_complete(target):
        return target

    archive = directory / Path(spec.url).name
    logger(f"[MODEL] {spec.label} ({target.name})")
    if not archive.exists():
        _download(spec.url, archive, logger)
    logger(f"[MODEL] Entpacke {archive.name} ...")
    with tarfile.open(archive, "r:bz2") as tar:
        try:
            tar.extractall(directory, filter="data")
        except TypeError:
            tar.extractall(directory)
    if _looks_complete(target):
        return target
    for candidate in sorted(directory.iterdir()):
        if candidate.is_dir() and _looks_complete(candidate):
            return candidate
    raise RuntimeError(f"Modell konnte nicht entpackt werden: {target}")


def _looks_complete(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_tokens = bool(list(path.glob("*tokens.txt")))
    has_encoder = bool(list(path.glob("*encoder*.onnx")))
    return has_tokens and has_encoder


def _pick(model_dir: Path, pattern: str) -> Path:
    candidates = sorted(model_dir.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"Keine Datei {pattern} in {model_dir}")
    int8 = [c for c in candidates if ".int8." in c.name]
    non_int8 = [c for c in candidates if ".int8." not in c.name]
    return (int8 or non_int8 or candidates)[0]


def find_model_files(model_dir: str | Path, *, require_joiner: bool = True) -> ModelFiles:
    model_dir = Path(model_dir)
    tokens = _pick(model_dir, "*tokens.txt")
    encoder = _pick(model_dir, "*encoder*.onnx")
    decoder = _pick(model_dir, "*decoder*.onnx")
    joiners = sorted(model_dir.glob("*joiner*.onnx"))
    joiner: Path | None = None
    if joiners:
        int8 = [c for c in joiners if ".int8." in c.name]
        joiner = (int8 or joiners)[0]
    if require_joiner and joiner is None:
        raise FileNotFoundError(f"Keine joiner*.onnx in {model_dir}")
    return ModelFiles(encoder=encoder, decoder=decoder, tokens=tokens, joiner=joiner)


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
class STTSession:
    def __init__(self, engine: "STTEngine", stream) -> None:
        self._engine = engine
        self._stream = stream
        self._warmed = False

    def _feed(self, samples: np.ndarray) -> None:
        engine = self._engine
        with engine.lock:
            self._stream.accept_waveform(engine.sample_rate, samples)
            while engine.recognizer.is_ready(self._stream):
                engine.recognizer.decode_stream(self._stream)

    def accept(self, samples: np.ndarray) -> None:
        engine = self._engine
        samples = np.asarray(samples, dtype=np.float32)
        if not self._warmed:
            self._warmed = True
            # Cache-aware Modelle (Nemotron) brauchen am Anfang etwas Audio,
            # sonst geht der Satzanfang verloren.
            if engine.lead_padding > 0:
                pad = np.zeros(int(engine.sample_rate * engine.lead_padding), dtype=np.float32)
                self._feed(pad)
        self._feed(samples)

    def result(self) -> str:
        with self._engine.lock:
            return self._engine.recognizer.get_result(self._stream)

    def reset(self) -> None:
        with self._engine.lock:
            self._engine.recognizer.reset(self._stream)
        self._warmed = False

    def finalize(self) -> str:
        engine = self._engine
        with engine.lock:
            # Tail-Padding: das letzte Chunk sicher auswerten.
            if engine.tail_padding > 0:
                pad = np.zeros(int(engine.sample_rate * engine.tail_padding), dtype=np.float32)
                self._stream.accept_waveform(engine.sample_rate, pad)
            self._stream.input_finished()
            while engine.recognizer.is_ready(self._stream):
                engine.recognizer.decode_stream(self._stream)
            return engine.recognizer.get_result(self._stream)


class STTEngine:
    kind = "streaming"

    def __init__(self, model_key, model_dir, *, languages=None, label="", sample_rate=16000,
                 num_threads=2, provider="cpu", model_type="", feature_dim=80,
                 language="", language_option=False, lead_padding=0.0, tail_padding=0.0,
                 logger: LogFn = log) -> None:
        self.model_key = model_key
        self.model_dir = Path(model_dir)
        self.label = label or model_key
        self.languages = languages or ["de"]
        self.sample_rate = sample_rate
        self.num_threads = num_threads
        self.provider = provider
        self.model_type = model_type
        self.feature_dim = feature_dim
        self.language = language
        self.language_option = language_option
        self.lead_padding = lead_padding
        self.tail_padding = tail_padding
        self.lock = threading.RLock()

        files = find_model_files(self.model_dir)
        logger(f"[STT] {self.label}: encoder={files.encoder.name}")
        logger(
            f"[STT] threads={num_threads} provider={provider} "
            f"model_type={model_type or '(auto)'} feature_dim={feature_dim}"
        )
        started = time.perf_counter()
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(files.tokens),
            encoder=str(files.encoder),
            decoder=str(files.decoder),
            joiner=str(files.joiner),
            num_threads=num_threads,
            sample_rate=sample_rate,
            feature_dim=feature_dim,
            provider=provider,
            model_type=model_type,
            enable_endpoint_detection=False,
            decoding_method="greedy_search",
        )
        self.load_seconds = time.perf_counter() - started
        logger(f"[STT] Recognizer bereit in {self.load_seconds:.2f}s")

    def create_session(self, language: str | None = None) -> STTSession:
        with self.lock:
            stream = self.recognizer.create_stream()
            if self.language_option:
                lang = (language or self.language or "auto").split(",")[0].strip() or "auto"
                try:
                    stream.set_option("language", lang)
                except Exception as exc:  # noqa: BLE001
                    logger(f"[STT] Sprache konnte nicht gesetzt werden: {exc}")
            return STTSession(self, stream)

    def info(self) -> dict:
        return {
            "kind": self.kind, "model": self.model_key, "label": self.label,
            "languages": self.languages, "language": self.language,
            "model_dir": str(self.model_dir), "sample_rate": self.sample_rate,
            "num_threads": self.num_threads, "provider": self.provider,
            "model_type": self.model_type or "(auto)",
            "feature_dim": self.feature_dim,
            "language_option": self.language_option,
            "sherpa_version": getattr(sherpa_onnx, "__version__", "?"),
            "load_seconds": round(self.load_seconds, 3),
        }


# ---------------------------------------------------------------------------
# Offline (Whisper, Canary)
# ---------------------------------------------------------------------------
class OfflineSTTSession:
    def __init__(self, engine: "OfflineSTTEngine") -> None:
        self._engine = engine
        self._chunks: list[np.ndarray] = []

    def accept(self, samples: np.ndarray) -> None:
        self._chunks.append(np.asarray(samples, dtype=np.float32))

    def result(self) -> str:
        return ""  # Offline-Modelle liefern keine Partials

    def reset(self) -> None:
        self._chunks = []

    def finalize(self) -> str:
        audio = (
            np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
        )
        self._chunks = []
        return self._engine.transcribe(audio)


class OfflineSTTEngine:
    def __init__(self, model_key, model_dir, *, kind, languages=None, label="", sample_rate=16000,
                 num_threads=2, provider="cpu", language="de", hotwords_file=None,
                 hotwords_score=2.5, bpe_vocab=None, logger: LogFn = log) -> None:
        self.kind = kind
        self.model_key = model_key
        self.model_dir = Path(model_dir)
        self.label = label or model_key
        self.languages = languages or [language or "de"]
        self.sample_rate = sample_rate
        self.num_threads = num_threads
        self.provider = provider
        self.language = language or "de"
        self.model_type = kind
        self.lock = threading.RLock()
        self.hotwords_file = str(hotwords_file) if hotwords_file else None
        self.hotwords_score = hotwords_score
        self.bpe_vocab = str(bpe_vocab) if bpe_vocab else None
        self.hotwords_count = 0
        self._alt_recognizers: dict = {}
        self._whisper_args: dict = {}
        if self.hotwords_file:
            try:
                with open(self.hotwords_file, encoding="utf-8") as handle:
                    self.hotwords_count = sum(1 for line in handle if line.strip())
            except OSError:
                self.hotwords_count = 0

        files = find_model_files(self.model_dir, require_joiner=kind != "whisper")
        logger(f"[STT] {self.label}: encoder={files.encoder.name} kind={kind} lang={self.language}")
        started = time.perf_counter()
        if kind == "parakeet":
            # NVIDIA NeMo Parakeet TDT (Transducer, Offline)
            kwargs: dict = {
                "encoder": str(files.encoder),
                "decoder": str(files.decoder),
                "joiner": str(files.joiner),
                "tokens": str(files.tokens),
                "num_threads": num_threads,
                "provider": provider,
                "model_type": "nemo_transducer",
            }
            # Contextual Biasing: nur mit bpe.vocab und Hotword-Datei moeglich.
            if self.hotwords_file and self.bpe_vocab and self.hotwords_count:
                kwargs.update(
                    decoding_method="modified_beam_search",
                    max_active_paths=8,
                    modeling_unit="bpe",
                    bpe_vocab=self.bpe_vocab,
                    hotwords_file=self.hotwords_file,
                    hotwords_score=hotwords_score,
                )
                logger(
                    f"[STT] Contextual Biasing aktiv: {self.hotwords_count} Hotwords "
                    f"(Score {hotwords_score})"
                )
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(**kwargs)
        elif kind == "whisper":
            # OpenAI Whisper (ONNX, Offline). Sprache/Task als Prompt.
            logger(f"[STT] Whisper: language={self.language} task=transcribe")
            self._whisper_args = {
                "encoder": str(files.encoder),
                "decoder": str(files.decoder),
                "tokens": str(files.tokens),
                "task": "transcribe",
                "num_threads": num_threads,
                "provider": provider,
            }
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                **self._whisper_args, language=self.language
            )
        else:
            raise ValueError(f"Unbekannte Offline-Art: {kind}")
        self.load_seconds = time.perf_counter() - started
        logger(f"[STT] Recognizer bereit in {self.load_seconds:.2f}s")

    def create_session(self, language: str | None = None) -> OfflineSTTSession:
        return OfflineSTTSession(self)

    def _recognizer_for(self, language: str | None):
        """Fuer Whisper ggf. einen Recognizer mit anderer Sprache bauen (Selbsttest)."""
        if self.kind != "whisper" or not language or language == self.language:
            return self.recognizer
        with self.lock:
            existing = self._alt_recognizers.get(language)
            if existing is None:
                existing = sherpa_onnx.OfflineRecognizer.from_whisper(
                    **self._whisper_args, language=language
                )
                self._alt_recognizers[language] = existing
            return existing

    def transcribe(self, samples: np.ndarray, language: str | None = None) -> str:
        recognizer = self._recognizer_for(language)
        with self.lock:
            stream = recognizer.create_stream()
            stream.accept_waveform(self.sample_rate, np.asarray(samples, dtype=np.float32))
            recognizer.decode_stream(stream)
            return stream.result.text

    def info(self) -> dict:
        return {
            "kind": self.kind, "model": self.model_key, "label": self.label,
            "languages": self.languages, "language": self.language,
            "model_dir": str(self.model_dir), "sample_rate": self.sample_rate,
            "num_threads": self.num_threads, "provider": self.provider,
            "model_type": self.kind,
            "hotwords_count": self.hotwords_count,
            "hotwords_active": bool(self.hotwords_file and self.bpe_vocab and self.hotwords_count),
            "hotwords_score": self.hotwords_score,
            "sherpa_version": getattr(sherpa_onnx, "__version__", "?"),
            "load_seconds": round(self.load_seconds, 3),
        }


def build_engine(
    model_key: str,
    model_url: str | None = None,
    num_threads: int = 2,
    model_type: str | None = None,
    languages: list[str] | None = None,
    kind: str | None = None,
    hotwords_file: str | None = None,
    hotwords_score: float = 2.5,
    logger: LogFn = log,
):
    spec = resolve_spec(model_key, model_url or None, model_type, kind)
    model_dir = ensure_model(spec, logger=logger)
    resolved_languages = languages or list(spec.languages)
    if spec.kind in ("streaming", "nemotron"):
        is_nemotron = spec.kind == "nemotron" or spec.model_type == "nemotron"
        if languages:
            default_language = languages[0]
        elif spec.language:
            default_language = spec.language
        elif is_nemotron:
            default_language = "auto"  # Sprach-Prompt: automatische Erkennung
        else:
            default_language = resolved_languages[0] if resolved_languages else "de"
        return STTEngine(
            spec.key, model_dir, languages=resolved_languages, label=spec.label,
            num_threads=num_threads, model_type=spec.model_type,
            feature_dim=128 if is_nemotron else spec.feature_dim,
            language=default_language,
            language_option=spec.language_option or is_nemotron,
            lead_padding=0.5 if is_nemotron else 0.0,
            tail_padding=0.66 if is_nemotron else 0.0,
            logger=logger,
        )
    source_language = (
        languages[0] if languages else (spec.language or (resolved_languages[0] if resolved_languages else "de"))
    ) or "de"
    bpe_vocab = None
    if spec.kind == "parakeet" and hotwords_file:
        from .hotwords import build_bpe_vocab

        bpe_vocab = build_bpe_vocab(model_dir)
    return OfflineSTTEngine(
        spec.key, model_dir, kind=spec.kind, languages=resolved_languages, label=spec.label,
        num_threads=num_threads, language=source_language,
        hotwords_file=hotwords_file, hotwords_score=hotwords_score, bpe_vocab=bpe_vocab,
        logger=logger,
    )


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """WAV-Bytes als Mono-``float32``-Samples plus Abtastrate einlesen."""
    with wave.open(io.BytesIO(data), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"nicht unterstuetzte Samplebreite: {width * 8} bit")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float32), rate


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Samples linear auf die Ziel-Abtastrate bringen (ausreichend fuer den Selbsttest)."""
    if source_rate == target_rate or len(samples) == 0:
        return np.asarray(samples, dtype=np.float32)
    duration = len(samples) / float(source_rate)
    target_length = max(1, int(round(duration * target_rate)))
    positions = np.linspace(0.0, len(samples) - 1, target_length, dtype=np.float64)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def transcribe_samples(
    engine, samples: np.ndarray, block_ms: int = 100, language: str | None = None
) -> tuple[str, float]:
    """Samples dekodieren und ``(Text, Dekodierzeit)`` zurueckgeben.

    Funktioniert fuer Streaming- und Offline-Engines (fuer den Selbsttest).
    """
    block = max(1, int(engine.sample_rate * block_ms / 1000))
    started = time.perf_counter()
    if engine.kind == "streaming":
        session = engine.create_session(language)
        for offset in range(0, len(samples), block):
            session.accept(samples[offset : offset + block])
        text = session.finalize()
    else:
        text = engine.transcribe(samples, language)
    return (text or "").strip(), time.perf_counter() - started
