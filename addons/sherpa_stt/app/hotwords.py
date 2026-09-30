"""Hotwords / Contextual Biasing.

Die Modelle erkennen seltene Eigennamen („Schreibtischlampe") schlecht. Mit
**Contextual Biasing** kann der Decoder auf eine Wortliste gelenkt werden.
sherpa-onnx unterstuetzt das nur fuer **Transducer** und nur mit
``modified_beam_search``; zusaetzlich braucht es eine ``bpe.vocab``, um die
Hotwords in BPE-Tokens zu zerlegen.

* **Parakeet v3** (offline Transducer) unterstuetzt das und profitiert deutlich.
* **Kroko** (streaming) liefert keine passende ``bpe.vocab`` mit; Tests zeigen
  dort praktisch keinen Effekt – Biasing wird deshalb nur fuer Offline-Modelle
  aktiviert.

Die ``bpe.vocab`` wird aus der ``tokens.txt`` des Modells erzeugt. Als Score
dient ``-token_id``: Das entspricht der Merge-Reihenfolge der BPE und hat sich
in Tests als wirksam erwiesen (mit Score 0 passiert fast nichts).
"""

from __future__ import annotations

from pathlib import Path


def parse_list(raw: str) -> list[str]:
    """Komma- oder zeilengetrennte Liste in einzelne Eintraege zerlegen."""
    items: list[str] = []
    for chunk in (raw or "").replace("\n", ",").replace(";", ",").split(","):
        item = " ".join(chunk.split())
        if item:
            items.append(item)
    return items


def build_bpe_vocab(model_dir: str | Path, dest: str | Path | None = None) -> Path | None:
    """``bpe.vocab`` aus ``tokens.txt`` erzeugen (Score = ``-token_id``)."""
    model_dir = Path(model_dir)
    tokens = model_dir / "tokens.txt"
    if not tokens.exists():
        return None
    dest = Path(dest) if dest else model_dir / "bpe.vocab"
    try:
        if dest.exists() and dest.stat().st_mtime >= tokens.stat().st_mtime:
            return dest
    except OSError:
        pass
    lines: list[str] = []
    for line in tokens.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        piece, _, index = line.rpartition(" ")
        try:
            score = -float(index)
        except ValueError:
            score = 0.0
        lines.append(f"{piece}\t{score}")
    try:
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return None
    return dest


def write_hotwords(words: list[str], dest: str | Path) -> int:
    """Hotword-Datei schreiben (dedupliziert, Mindestlaenge 3). Gibt Anzahl zurueck."""
    seen: set[str] = set()
    output: list[str] = []
    for word in words:
        item = " ".join(str(word).split())
        key = item.lower()
        if len(item) < 3 or key in seen:
            continue
        seen.add(key)
        output.append(item)
    dest = Path(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(output) + ("\n" if output else ""), encoding="utf-8")
    except OSError:
        return 0
    return len(output)
