"""CLI-Einstieg: Mikrofon/WAV/Text -> Pipeline -> Terminal.

Beispiele:
    python main.py --debug
    python main.py --text "mach das licht im wohnzimmer an"
    python main.py --wav models/.../test_wavs/0.wav
    python main.py --list-devices
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import config
from events import EventBus
from pipeline import VoicePipeline


def _section(title: str) -> None:
    print(f"\n{'-' * 44}\n{title}\n{'-' * 44}")


class TerminalPrinter:
    def __init__(self, debug: bool = False) -> None:
        self.debug = debug

    def handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "stt_partial":
            print(f"[STT][partial] {event['text']}")
        elif kind == "stt_final":
            latency = event.get("latency_ms")
            suffix = f"  ({latency:.0f} ms)" if latency and self.debug else ""
            print(f"[STT][final]   {event['text']}{suffix}")
        elif kind == "user_text":
            _section("EINGABE")
            print(event["text"])
        elif kind == "needle":
            self._needle(event)
        elif kind == "needle_refusal":
            print("\n[NEEDLE] Kein passender Tool-Call (Refusal)")
        elif kind == "needle_suppressed":
            print(f"\n[NEEDLE] Call unterdrueckt: {json.dumps(event['calls'], ensure_ascii=False)}")
        elif kind == "low_confidence":
            conf = event.get("confidence")
            print(
                f"\n[NEEDLE] LOW CONFIDENCE ({conf} < {event['threshold']}) – nicht ausgefuehrt"
            )
        elif kind == "needle_error":
            print(f"\n[NEEDLE ERROR] {event.get('error_code')}: {event.get('error')}")
        elif kind == "tool_call":
            print(f"\n[TOOL CALL] {event['name']}({json.dumps(event['arguments'], ensure_ascii=False)})")
        elif kind == "tool_result":
            print(f"[TOOL RESULT] {json.dumps(event['result'], ensure_ascii=False)}")
        elif kind == "needle_final":
            print("\n[NEEDLE FINAL]")
            print(json.dumps(event["response"], ensure_ascii=False, indent=2))
        elif kind == "error":
            print(f"\n[{event.get('source', 'ERROR').upper()} ERROR] {event.get('message')}")
        elif kind == "mic":
            print(f"[MIC] {'an' if event['running'] else 'aus'}")
        elif kind == "status":
            print(f"[STATUS] {event['message']}")
        elif kind == "dry_run":
            print(f"[HA] Dry-Run {'an' if event['value'] else 'aus'}")
        elif kind == "state_changed" and self.debug:
            print(f"[HA] {event['entity_id']} -> {event.get('new_state')}")

    def _needle(self, event: dict) -> None:
        response = event["response"]
        _section("NEEDLE 3")
        print(f"type: {response.get('type')}   success: {response.get('success')}")
        print(f"confidence: {response.get('confidence')}")
        if response.get("reasoning"):
            print(f"\nreasoning:\n{response['reasoning']}")
        if event.get("calls"):
            print("\nfunction_calls:")
            print(json.dumps(event["calls"], ensure_ascii=False, indent=2))
        if self.debug:
            print(
                "\nmetrics:"
                f" prefill_tps={response.get('prefill_tps')}"
                f" decode_tps={response.get('decode_tps')}"
                f" peak_ram_mb={response.get('peak_ram_mb')}"
                f" needle_latency_ms={event.get('latency_ms', 0):.0f}"
            )


def _wait_for_utterance(
    bus: EventBus, channel, printer: TerminalPrinter, timeout: float = 60.0
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            event = channel.get(timeout=0.5)
        except Exception:  # noqa: BLE001
            continue
        if event.get("type") == "utterance_end":
            return
        printer.handle(event)


def build_pipeline(args: argparse.Namespace, bus: EventBus, printer: TerminalPrinter) -> VoicePipeline:
    if args.mock is not None:
        config.HA_USE_MOCK = args.mock
    dry_run = args.dry_run
    if args.no_dry_run:
        dry_run = False
    if dry_run is None:
        dry_run = config.HA_DRY_RUN
    return VoicePipeline(
        debug=args.debug,
        dry_run=dry_run,
        bus=bus,
        logger=print,
        load_stt=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lokaler Voice-Assistant (STT -> Needle 3 -> HA)")
    parser.add_argument("--debug", action="store_true", help="Metriken und Latenzen ausgeben")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=None, help="HA-Aktionen nur simulieren")
    parser.add_argument("--no-dry-run", dest="no_dry_run", action="store_true", help="HA-Aktionen wirklich ausfuehren")
    parser.add_argument("--mock", dest="mock", action="store_true", default=None, help="Mock-Backend erzwingen")
    parser.add_argument("--no-mock", dest="mock", action="store_false", help="echtes Home Assistant nutzen")
    parser.add_argument("--text", help="einen Text direkt verarbeiten (ohne Mikrofon)")
    parser.add_argument("--wav", help="eine WAV-Datei transkribieren und verarbeiten")
    parser.add_argument("--list-devices", action="store_true", help="Audio-Eingabegeraete auflisten")
    parser.add_argument("--web", action="store_true", help="Web-UI statt Terminal starten")
    parser.add_argument("--host", default=config.WEBUI_HOST, help="Web-UI Host")
    parser.add_argument("--port", type=int, default=config.WEBUI_PORT, help="Web-UI Port")
    args = parser.parse_args(argv)

    if args.list_devices:
        from audio.microphone import list_input_devices

        for index, name, channels in list_input_devices():
            print(f"[{index}] {name} ({channels} ch)")
        return 0

    if args.mock is not None:
        config.HA_USE_MOCK = args.mock
    if args.dry_run:
        config.HA_DRY_RUN = True
    if args.no_dry_run:
        config.HA_DRY_RUN = False

    if args.web:
        import uvicorn

        from webui.app import create_app

        print(f"[WEBUI] http://{args.host}:{args.port}")
        uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
        return 0

    bus = EventBus()
    channel = bus.subscribe()
    printer = TerminalPrinter(debug=args.debug)

    if args.wav:
        from stt.zipformer import transcribe_file

        pipeline = build_pipeline(args, bus, printer)
        _section("STT")
        final, partials = transcribe_file(args.wav, pipeline.stt)
        for partial in partials:
            marker = "final" if partial == final else "partial"
            print(f"[STT][{marker:7}] {partial}")
        if final:
            pipeline.process_text(final)
        while True:
            try:
                printer.handle(channel.get_nowait())
            except Exception:  # noqa: BLE001
                break
        pipeline.stop()
        return 0

    pipeline = build_pipeline(args, bus, printer)
    pipeline.start()

    if args.text:
        _section("PIPELINE")
        pipeline.submit_text(args.text)
        _wait_for_utterance(bus, channel, printer)
        pipeline.stop()
        return 0

    print("Mikrofon aktiv. Strg+C zum Beenden.")
    try:
        pipeline.start_microphone()
        while True:
            try:
                printer.handle(channel.get(timeout=0.5))
            except Exception:  # noqa: BLE001
                pass
    except KeyboardInterrupt:
        print("\nBeende ...")
    finally:
        pipeline.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
