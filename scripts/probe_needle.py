"""Phase-3-Probe: Needle isoliert, ohne Mikrofon.

Testet deutsche und englische Saetze gegen die Dummy-Tools und zeigt die
komplette Response inklusive Metriken.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.needle import NeedleAgent, confidence, function_calls, is_refusal
from tools.dummy import build_dummy_registry

GERMAN = [
    "mach das licht im wohnzimmer an",
    "schalte die stehlampe im wohnzimmer aus",
    "mach die kaffeemaschine an",
    "wie ist der zustand vom kuechenlicht",
    "stell den fernseher auf lautstaerke 0,3",
    "wie ist das wetter morgen",
]

ENGLISH = [
    "turn on the living room ceiling lamp",
    "turn off the living room floor lamp",
    "turn on the coffee machine",
    "what is the state of the kitchen light",
    "set the tv volume to 0.3",
    "what is the weather tomorrow",
]


def run(agent: NeedleAgent, sentences: list[str], label: str) -> None:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    for sentence in sentences:
        response = agent.complete(sentence)
        calls = function_calls(response)
        conf = confidence(response)
        print(f"\n> {sentence!r}")
        print(
            f"  type={response.get('type')} success={response.get('success')} "
            f"confidence={conf}"
        )
        if is_refusal(response):
            print("  -> keine passende Funktion (Refusal)")
        else:
            print(f"  -> {json.dumps(calls, ensure_ascii=False)}")
        if response.get("reasoning"):
            print(f"  reasoning: {response['reasoning']}")
        if response.get("error"):
            print(f"  error: {response.get('error_code')} {response.get('error')}")
        print(
            "  metrics:"
            f" prefill_tps={response.get('prefill_tps')}"
            f" decode_tps={response.get('decode_tps')}"
            f" peak_ram_mb={response.get('peak_ram_mb')}"
        )
        agent.reset()


def main() -> int:
    registry = build_dummy_registry()
    print(f"Tools ({len(registry)}): {', '.join(registry.names())}")
    print("Lade Needle 3 (erster Start laedt Engine + Weights von HuggingFace) ...")
    agent = NeedleAgent(registry, system="locale: de-DE; device: pc")
    print("Needle bereit.")
    run(agent, GERMAN, "DEUTSCH")
    run(agent, ENGLISH, "ENGLISH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
