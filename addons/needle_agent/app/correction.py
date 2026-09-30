"""STT-Autokorrektur.

Spracherkennung verwechselt oft aehnlich klingende Woerter
("kaffemaschine" statt "Kaffeemaschine", "schreibtischlame" statt
"Schreibtischlampe"). Besonders im Deutschen kommt hinzu, dass
**Komposita falsch getrennt** werden:

    "Schreibt die Schlampe 20%"  ->  gemeint: "Schreibtischlampe 20%"

Dieses Modul korrigiert deshalb in **zwei Stufen**:

1. **Zusammengezogene Woerter (Spans):** benachbarte Tokens werden
   zusammengesetzt und gegen die Entity-Namen/Aliase geprueft (mit
   Kölner Phonetik + RapidFuzz). Damit wird "schreibt die schlampe" zu
   "Schreibtischlampe" und umgekehrt "wohnzimmerdeckenlampe" zu
   "Wohnzimmer Deckenlampe".
2. **Einzelne Tokens:** wie bisher ("schreibtischlame" -> "Schreibtischlampe").

Sicherheitsnetz: korrigiert wird nur, wenn das Ergebnis ein Kommando
**auflösbar** macht; Kommandoverben und bereits bekannte Woerter bleiben
unangetastet, Zahlen werden nie verändert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from rapidfuzz import fuzz

from .commands import (
    DIM_WORDS,
    OFF_WORDS,
    ON_WORDS,
    STATE_WORDS,
    TEMP_WORDS,
    VOLUME_WORDS,
)
from .entities import normalize
from .matching import TYPE_KEYWORDS, Resolver, koelner_phonetik

Reranker = Callable[[str, list[str]], str | None]

# Kommandoverben/Füllwörter: werden NIE korrigiert
PROTECTED = {
    "schalte", "schalt", "schalten", "mache", "mach", "machen", "drehe", "dreh",
    "stelle", "stell", "stellen", "setze", "setz", "setzen", "dimme", "dimmen",
    "gib", "zeige", "sag", "bitte", "moechte", "will", "kann", "koennte", "wuerde",
}

# Funktionswörter, die in einem zusammengesetzten Namen stehen dürfen.
FUNCTION_WORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "eines", "im", "in", "am", "an", "auf", "unter", "ueber", "vor", "hinter",
    "neben", "bei", "zu", "zur", "zum", "von", "vom", "mit", "und", "oder",
    "mein", "meine", "meinen", "dein", "deine", "unser", "unsere",
}

# Kommando-Wörter: dürfen **nie** an den Rändern eines Spans liegen
# ("schreibtischlame ein" darf nicht zu "Schreibtischlampe" verschmelzen).
COMMAND_WORDS = (
    set(ON_WORDS) | set(OFF_WORDS) | set(DIM_WORDS)
    | set(VOLUME_WORDS) | set(TEMP_WORDS) | set(STATE_WORDS)
)

# Maximale Anzahl Tokens, die zu einem Namen zusammengezogen werden.
MAX_SPAN = 4

_WORD_RE = re.compile(r"^(\W*)(.*?)(\W*)$", re.S)


@dataclass
class Correction:
    original: str
    corrected: str
    score: float
    reason: str


@dataclass
class _Phrase:
    spaced: str
    compact: str
    original: str


def _vocabulary(resolver: Resolver) -> dict[str, str]:
    """normalisiertes Token -> kanonisches Wort (bevorzugt das kürzeste)."""
    vocab: dict[str, str] = {}

    def add(word: str) -> None:
        for token in normalize(word).split():
            if len(token) < 4:
                continue
            current = vocab.get(token)
            if current is None or len(word) < len(current):
                vocab[token] = word

    for entity in resolver.entities:
        add(entity.name)
        for alias in entity.aliases:
            add(alias)
        if entity.area:
            add(entity.area)
        if entity.floor:
            add(entity.floor)
    for keywords in TYPE_KEYWORDS.values():
        for keyword in keywords:
            add(keyword)
    for words in (ON_WORDS, OFF_WORDS, DIM_WORDS, VOLUME_WORDS, TEMP_WORDS, STATE_WORDS):
        for word in words:
            add(word)
    return vocab


def _phrases(resolver: Resolver) -> list[_Phrase]:
    """Alle sprechbaren Namen (Entity-Namen, Aliase, Bereiche) als Phrasen."""
    phrases: dict[str, _Phrase] = {}

    def add(word: str) -> None:
        spaced = normalize(word)
        if not spaced:
            return
        compact = spaced.replace(" ", "")
        if len(compact) < 4:
            return
        if compact not in phrases:
            phrases[compact] = _Phrase(spaced=spaced, compact=compact, original=word)

    for entity in resolver.entities:
        add(entity.name)
        for alias in entity.aliases:
            add(alias)
        if entity.area:
            add(entity.area)
        if entity.floor:
            add(entity.floor)
    return list(phrases.values())


class Corrector:
    def __init__(
        self,
        resolver: Resolver,
        *,
        enabled: bool = True,
        spans: bool = True,
        min_score: float = 0.8,
        min_margin: float = 0.05,
        max_length_diff: int = 3,
        reranker: Reranker | None = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.resolver = resolver
        self.enabled = enabled
        self.spans = spans
        self.min_score = min_score
        self.min_margin = min_margin
        self.max_length_diff = max_length_diff
        self.reranker = reranker
        self.logger = logger
        self.vocabulary = _vocabulary(resolver)
        self.phrases = _phrases(resolver)

    # -- Einzelwort-Kandidaten --------------------------------------------
    def candidates(self, token: str) -> list[tuple[str, str, float]]:
        """(normalisiert, kanonisch, score) fuer ein Token, absteigend sortiert."""
        key = normalize(token)
        if len(key) < 4:
            return []
        if key in PROTECTED or key in self.vocabulary:
            return []  # Kommandowort oder bereits korrekt
        code = koelner_phonetik(key)
        results: list[tuple[str, str, float]] = []
        for vocab_key, canonical in self.vocabulary.items():
            if abs(len(vocab_key) - len(key)) > self.max_length_diff:
                continue
            score = 0.0
            reason = ""
            if vocab_key == key:
                continue  # schon korrekt
            if code and code == koelner_phonetik(vocab_key):
                score = 0.95
                reason = "phonetisch"
            ratio = fuzz.ratio(key, vocab_key) / 100.0
            if ratio > score:
                score = ratio
                reason = "ähnlich"
            if score < self.min_score:
                continue
            if key[:1] == vocab_key[:1]:
                score = min(1.0, score + 0.03)
            results.append((vocab_key, canonical, round(score, 3)))
        results.sort(key=lambda item: item[2], reverse=True)
        # Fast-Duplikate entfernen (z. B. "fernseh" neben "fernseher")
        kept: list[tuple[str, str, float]] = []
        for item in results:
            if any(fuzz.ratio(item[0], other[0]) / 100.0 >= 0.9 for other in kept):
                continue
            kept.append(item)
        return kept

    def _correct_tokens(self, text: str) -> tuple[str, list[Correction]]:
        corrections: list[Correction] = []
        output: list[str] = []
        for raw in text.split():
            prefix, core, suffix = _WORD_RE.match(raw).groups()
            if not core:
                output.append(raw)
                continue
            options = self.candidates(core)
            if not options:
                output.append(raw)
                continue

            chosen: tuple[str, str, float] | None = None
            if len(options) > 1 and self.reranker is not None:
                names = [canonical for _, canonical, _ in options[:5]]
                picked = self.reranker(text, names)
                if picked:
                    for vocab_key, canonical, score in options:
                        if canonical == picked or vocab_key == normalize(picked):
                            chosen = (vocab_key, canonical, score)
                            break
            if chosen is None:
                best = options[0]
                margin_ok = len(options) == 1 or (best[2] - options[1][2]) >= self.min_margin
                if not margin_ok:
                    output.append(raw)
                    continue
                chosen = best

            vocab_key, canonical, score = chosen
            corrections.append(
                Correction(original=core, corrected=canonical, score=score, reason="Autokorrektur")
            )
            output.append(f"{prefix}{canonical}{suffix}")

        if not corrections:
            return text, []
        return " ".join(output), corrections

    # -- Zusammengezogene Woerter (Spans) ---------------------------------
    def _span_tokens(self, text: str) -> list[tuple[str, str, str]]:
        parts = []
        for raw in text.split():
            prefix, core, suffix = _WORD_RE.match(raw).groups()
            parts.append((prefix, core, suffix))
        return parts

    def _span_usable(self, cores: list[str]) -> tuple[bool, bool]:
        """(verwendbar, enthaelt_bekanntes_wort)."""
        if normalize(cores[0]) in COMMAND_WORDS or normalize(cores[-1]) in COMMAND_WORDS:
            return False, False  # Kommando-Wort am Rand -> gehoert zum Befehl
        suspicious = 0
        has_known = False
        for core in cores:
            key = normalize(core)
            if not key or any(char.isdigit() for char in core):
                return False, False
            if key in PROTECTED:
                return False, False
            if key in self.vocabulary:
                has_known = True
                continue
            if key in FUNCTION_WORDS:
                continue
            if len(key) >= 3:
                suspicious += 1
        return suspicious >= 1, has_known

    def _best_phrase(self, span_compact: str, span_spaced: str, has_known: bool):
        threshold = 0.92 if has_known else self.min_score
        code = koelner_phonetik(span_compact)
        max_diff = self.max_length_diff + max(0, len(span_compact) - len(span_spaced)) * 2
        results: list[tuple[float, str, str]] = []
        for phrase in self.phrases:
            if abs(len(phrase.compact) - len(span_compact)) > max_diff:
                continue
            score = max(
                fuzz.ratio(span_compact, phrase.compact) / 100.0,
                fuzz.ratio(span_spaced, phrase.spaced) / 100.0,
            )
            if code and code == koelner_phonetik(phrase.compact):
                score = max(score, 0.95)
            if score < threshold:
                continue
            results.append((round(score, 3), phrase.original, phrase.compact))
        results.sort(key=lambda item: item[0], reverse=True)
        if not results:
            return None
        best = results[0]
        margin_ok = len(results) == 1 or (best[0] - results[1][0]) >= self.min_margin
        if not margin_ok and best[0] < 0.95:
            return None
        return best

    def _correct_spans(
        self,
        text: str,
        validate: Callable[[str], bool] | None,
    ) -> tuple[str, list[Correction]]:
        parts = self._span_tokens(text)
        cores = [core for _, core, _ in parts]
        count = len(cores)
        if count < 1:
            return text, []

        used: set[int] = set()
        replacements: dict[int, tuple[int, str, float, str]] = {}
        for size in range(min(MAX_SPAN, count), 0, -1):
            for start in range(0, count - size + 1):
                if any(index in used for index in range(start, start + size)):
                    continue
                span = cores[start : start + size]
                usable, has_known = self._span_usable(span)
                if not usable:
                    continue
                span_spaced = normalize(" ".join(span))
                span_compact = span_spaced.replace(" ", "")
                if len(span_compact) < 5:
                    continue
                match = self._best_phrase(span_compact, span_spaced, has_known)
                if match is None:
                    continue
                score, phrase, phrase_compact = match
                if size == 1 and " " not in normalize(phrase):
                    # Nur mehrteilige Namen duerfen ein einzelnes Token ersetzen
                    # ("wohnzimmerdeckenlampe" -> "Wohnzimmer Deckenlampe");
                    # Tippfehler einzelner Woerter macht die Token-Stufe.
                    continue
                prefix = parts[start][0]
                suffix = parts[start + size - 1][2]
                trial = [part[0] + part[1] + part[2] for part in parts]
                trial[start : start + size] = [f"{prefix}{phrase}{suffix}"]
                trial_text = " ".join(trial)
                if validate is not None and not validate(trial_text):
                    continue
                replacements[start] = (size, phrase, score, " ".join(span))
                used.update(range(start, start + size))

        if not replacements:
            return text, []
        output: list[str] = []
        corrections: list[Correction] = []
        index = 0
        while index < count:
            if index in replacements:
                size, phrase, score, original = replacements[index]
                prefix = parts[index][0]
                suffix = parts[index + size - 1][2]
                output.append(f"{prefix}{phrase}{suffix}")
                corrections.append(
                    Correction(original=original, corrected=phrase, score=score, reason="Kompositum")
                )
                index += size
            else:
                prefix, core, suffix = parts[index]
                output.append(f"{prefix}{core}{suffix}")
                index += 1
        return " ".join(output), corrections

    # -- Gesamtkorrektur ---------------------------------------------------
    def correct(
        self,
        text: str,
        validate: Callable[[str], bool] | None = None,
    ) -> tuple[str, list[Correction]]:
        if not self.enabled or not text:
            return text, []
        if validate is not None and validate(text):
            return text, []  # Original funktioniert bereits

        span_text, span_corrections = (
            self._correct_spans(text, validate) if self.spans else (text, [])
        )
        token_text, token_corrections = self._correct_tokens(span_text)

        if not span_corrections and not token_corrections:
            return text, []

        combined = token_text if token_corrections else span_text
        combined_corrections = span_corrections + token_corrections

        if validate is None or validate(combined):
            corrections = combined_corrections
        elif span_corrections and validate(span_text):
            combined, corrections = span_text, span_corrections
        elif all(c.score >= 0.9 for c in combined_corrections):
            corrections = combined_corrections
        else:
            return text, []

        self.logger(
            "[CORRECTION] "
            + ", ".join(f"'{c.original}' -> '{c.corrected}' ({c.score}, {c.reason})" for c in corrections)
        )
        return combined, corrections
