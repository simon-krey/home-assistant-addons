# Voice Assistant – lokaler Prototyp

Lokale Sprachpipeline:

```text
Mikrofon
   ↓
Streaming Zipformer (sherpa-onnx)
   ↓
Final Transcript
   ↓
Needle 3 (Tool Calling)
   ↓
Tool-Dispatcher
   ↓
Home Assistant (WebSocket) / Mock
```

Das Projekt folgt den Plänen `plan.md` (PC-Prototyp) und `additional-plan.md`
(Home-Assistant-Integration). Tools werden zunächst im **Dry-Run** ausgeführt,
d. h. der geplante Home-Assistant-Aufruf wird nur angezeigt.

## Home Assistant Add-ons

Dieses Repository ist **gleichzeitig ein HA-Add-on-Repository**. Beide Add-ons
werden über dieselbe GitHub-URL erkannt:

| Add-on | Zweck |
|---|---|
| **Sherpa STT (Wyoming)** | Lokales STT (Streaming-Zipformer, Whisper int8, Canary) als Wyoming-Server → in den Assist-Einstellungen nativ als Sprache-zu-Text auswählbar, mit Web-UI und Verlauf inkl. Audio-Samples |
| **Needle 3 Conversation** | Conversation-Agent (Wyoming `handle`) mit wählbarem Backend: Needle 3, OpenAI-kompatible LLMs (Ollama/llama.cpp) oder Home Assists Agent → in den Assist-Einstellungen als Conversation auswählbar, mit Tool-Calling, Grounding-Prüfung, HA-Fallback, Web-UI und Verlauf |
| **STT Realtime Test** | Benchmark-UI für die Echtzeitfähigkeit der STT auf dem Pi (RTF, Datei-Benchmark, Mikrofon-Test) |

Installation: GitHub-URL in **Einstellungen → Add-ons → Add-on-Store →
Repositories** eintragen. Details in [`addons/README.md`](addons/README.md).

## Status

| Phase | Inhalt | Status |
|---|---|---|
| 0 | Setup, Struktur, Config | ✅ |
| 1 | Audio (sounddevice + Queue) | ✅ |
| 2 | Streaming-STT (deutsches Zipformer) | ✅ |
| 3 | Needle 3 isoliert (DE getestet) | ✅ |
| 4 | Pipeline + Terminal | ✅ |
| 5 | Home Assistant (Mock + echter WS-Client) | ✅ |
| 6 | Web-UI | ✅ |

## Installation

```bash
uv venv --python 3.14 .venv
uv pip install -r requirements.txt
cp .env.example .env
```

Beim ersten Needle-Start werden Engine und Weights einmalig von Hugging Face
geladen (`~/.cache/cactus-needle`). Danach läuft die Inferenz offline.

### STT-Modell

Das deutsche Streaming-Modell wird nach `models/` entpackt:

```bash
mkdir -p models && cd models
wget https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06.tar.bz2
tar xjf sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06.tar.bz2
rm sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06.tar.bz2
```

## Nutzung

```bash
# Mikrofon -> Pipeline -> Terminal
.venv/bin/python main.py --debug

# Einzelnen Text ohne Mikrofon verarbeiten
.venv/bin/python main.py --text "mach das licht im wohnzimmer an"

# WAV-Datei transkribieren und verarbeiten
.venv/bin/python main.py --wav models/sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06/test_wavs/0.wav

# Audio-Eingabegeräte auflisten
.venv/bin/python main.py --list-devices

# Web-UI (empfohlen zum Testen)
.venv/bin/python main.py --web            # http://127.0.0.1:8000
```

Wichtige CLI-Flags: `--debug`, `--dry-run` / `--no-dry-run`,
`--mock` / `--no-mock`, `--text`, `--wav`, `--web`.

## Web-UI

`python main.py --web` öffnet eine Testoberfläche mit:

- **Texteingabe** (ohne Mikrofon) zum schnellen Testen
- **Mikrofon** Start/Stop (server-seitig)
- **Dry-Run**-Umschalter
- **Live-Event-Log** per WebSocket (Partial/Final, Needle, Tool-Calls, Fehler)
- **Entity-/State-Tabelle**

## Home Assistant

Standardmäßig ist das **Mock-Backend** aktiv (`HA_USE_MOCK=true`). Für eine
echte Instanz in `.env` setzen:

```text
HA_USE_MOCK=false
HA_DRY_RUN=true
HA_URL=ws://192.168.x.x:8123/api/websocket
HA_TOKEN=<long-lived access token>
```

Der Client (`homeassistant/client.py`) läuft in einem eigenen Event-Loop-Thread,
authentifiziert sich per Long-Lived Token, lädt States/Entities und abonniert
`state_changed`. Bei Verbindungsverlust wird automatisch neu verbunden.

## Architektur

```text
config.py            Konfiguration (ENV/.env)
events.py            Event-Bus (Terminal + Web-UI)
pipeline.py          Worker-Threads + Queues + Dispatcher
main.py              CLI
audio/               Audioquellen (Mikrofon, WAV)
stt/                 Streaming-STT (sherpa-onnx)
llm/                 Needle-3-Agent (complete + Result-Flow)
tools/               Tool-Registry + Dummy-Tools
homeassistant/       Backend-Interface, Mock, WebSocket-Client, Tools, State
webui/               FastAPI + Single-Page-UI
repository.yaml      macht den Repo-Root zum HA-Add-on-Repository
addons/              Home-Assistant-Add-ons (sherpa_stt, needle_agent, stt_test)
```

Die Module sind nicht direkt gekoppelt: `stt` → Queue → `pipeline` → Queue →
`llm`/`tools`. Dadurch bleibt die Audioquelle austauschbar.

### Tools

Die Tool-Schemas werden **dynamisch aus den Home-Assistant-Entities** erzeugt;
die Gerätenamen landen als `enum` im Decode-Grammar. Dadurch kann Needle nur
existierende Geräte wählen, Python übersetzt Name → `entity_id`.

Es werden bewusst höchstens fünf Tools angeboten (`turn_on_light`,
`turn_off_light`, `turn_on_switch`, `get_entity_state`, `set_media_volume`),
weil Needle ab mehr als fünf Tools ein Retrieval aktiviert.

## Tests / Probes

```bash
# Needle isoliert (DE/EN)
.venv/bin/python scripts/probe_needle.py
```
