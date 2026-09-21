# Voice Assistant – Home Assistant Add-ons

Dieses Repository ist gleichzeitig ein **Home-Assistant-Add-on-Repository**.
Beide Add-ons werden über **dieselbe Repo-URL** erkannt.

## Add-ons

| Add-on | Slug | Zweck |
|---|---|---|
| **Sherpa STT (Wyoming)** | `sherpa_stt` | Lokales Streaming-/Offline-STT als Wyoming-Server – nativ in den Assist-Einstellungen als Sprache-zu-Text auswählbar, mit Web-UI und Verlauf inkl. Audio-Samples |
| **Needle 3 Conversation** | `needle_agent` | Conversation-Agent (Wyoming `handle`) auf Basis von **Needle 3** – mit **deterministischem Fast-Path** für deutsche Kommandos, **Fuzzy-/Phonetik-Namensauflösung** (Kölner Phonetik, RapidFuzz, Aliase, Areas/Floors), Grounding-/Polaritätsprüfung, HA-Fallback, Web-UI und Verlauf |
| **STT Realtime Test** | `stt_test` | Benchmark-UI, um die Echtzeitfähigkeit der STT auf dem Pi zu messen (RTF, Datei-Benchmark, Mikrofon-Test) |

## Installation

1. Repository auf GitHub hochladen. Wichtig: `repository.yaml` liegt im
   **Repo-Root**.
2. In Home Assistant: **Einstellungen → Add-ons → Add-on-Store → ⋮ →
   Repositories** → GitHub-URL eintragen.
3. Beide Add-ons erscheinen im Store und können einzeln installiert werden.

```text
<repo-root>/
├── repository.yaml          # macht den Ordner zum Add-on-Repository
└── addons/
    ├── sherpa_stt/          # Wyoming-STT-Add-on
    │   ├── config.yaml
    │   ├── Dockerfile
    │   └── app/…
    ├── needle_agent/        # Wyoming-Conversation-Add-on (Needle 3)
    │   ├── config.yaml
    │   ├── Dockerfile
    │   └── app/…
    └── stt_test/            # Realtime-Test-Add-on
        ├── config.yaml
        ├── Dockerfile
        └── …
```

## Warum funktioniert das?

Der Supervisor sucht im Repository **rekursiv** nach `config.*`
(`path.glob("**/config.*")`, ausgenommen versteckte Ordner und `rootfs`).
Solange `repository.yaml` im Root liegt, findet er jedes Add-on in jeder
Unterordner-Tiefe. Deshalb liegen hier beide Add-ons unter `addons/`.

Wichtig: Im Repository darf **keine andere Datei** `config.yaml`, `config.yml`
oder `config.json` heißen – sonst wird sie als Add-on fehlinterpretiert.

## Hinweis zu HAOS und Docker

Auf Home Assistant OS läuft Docker intern, wird aber vom Supervisor verwaltet.
Eigene Container startet man dort **nicht** per `docker run`, sondern als
**Add-on** – genau das sind diese beiden Ordner. Für Raspberry Pi OS o. Ä. kann
man dieselben Dockerfiles auch direkt mit `docker build`/`docker compose`
nutzen (siehe `addons/stt_test/README.md`).
