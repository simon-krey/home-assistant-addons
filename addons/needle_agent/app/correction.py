"""STT-Autokorrektur.

Spracherkennung verwechselt oft aehnlich klingende Woerter
("kaffemaschine" statt "Kaffeemaschine", "schreibtischlame" statt
"Schreibtischlampe"). Dieses Modul bildet solche Tokens auf das
Domaenen-Vokabular ab:

* Vokabular aus Entity-Namen, Aliasen, Areas/Floors und Kommandowörtern
* Kandidaten ueber **Kölner Phonetik** und **RapidFuzz**
* nur eindeutige Treffer (Score + Vorsprung), begrenzte Laengendifferenz
* **Validierung**: korrigiert wird nur, wenn das Ergebnis ein Kommando
  auflösbar macht – normale Woerter bleiben unangetastet
* optionales **Reranking** durch ein Entscheidungsmodell (Laya)
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


@dataclass
class Correction:
    original: str
    corrected: str
    score: float
    reason: str


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


class Corrector:
    def __init__(
        self,
        resolver: Resolver,
        *,
        enabled: bool = True,
        min_score: float = 0.8,
        min_margin: float = 0.05,
        max_length_diff: int = 3,
        reranker: Reranker | None = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.resolver = resolver
        self.enabled = enabled
        self.min_score = min_score
        self.min_margin = min_margin
        self.max_length_diff = max_length_diff
        self.reranker = reranker
        self.logger = logger
        self.vocabulary = _vocabulary(resolver)

    # -- Kandidaten --------------------------------------------------------
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

    # -- Korrektur ---------------------------------------------------------
    def correct(
        self,
        text: str,
        validate: Callable[[str], bool] | None = None,
    ) -> tuple[str, list[Correction]]:
        if not self.enabled or not text:
            return text, []
        if validate is not None and validate(text):
            return text, []  # Original funktioniert bereits

        corrections: list[Correction] = []
        output: list[str] = []
        for raw in text.split():
            prefix, core, suffix = re.match(r"^(\W*)(.*?)(\W*)$", raw, re.S).groups()
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
        corrected = " ".join(output)
        if validate is not None and not validate(corrected):
            # Korrektur macht nichts auflösbar -> nur bei sehr hoher Sicherheit übernehmen
            if any(c.score < 0.9 for c in corrections):
                return text, []
        self.logger(
            "[CORRECTION] " + ", ".join(f"'{c.original}' -> '{c.corrected}' ({c.score})" for c in corrections)
        )
        return corrected, corrections
