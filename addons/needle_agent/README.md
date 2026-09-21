# Needle 3 Conversation

**Conversation-Agent** für Home Assistant über das **Wyoming-Protokoll**
(`handle`-Domain). Dadurch erscheint er nativ unter **Einstellungen →
Sprachassistenten → Assistant → Conversation** und lässt sich mit der
View Assist Companion App bzw. jeder Assist-Pipeline nutzen.

```text
View Assist / Assist
   ├── Sprache-zu-Text   → sherpa_stt   (Wyoming asr)
   ├── Conversation      → dieses Add-on (Wyoming handle)
   └── Text-zu-Sprache   → Piper        (Wyoming tts)
```

## Backends (im UI wählbar)

| Backend | Was es tut | Wann sinnvoll |
|---|---|---|
| **`llama_cpp`** | **Eingebautes** llama.cpp mit GGUF-Modellen – lädt Modelle automatisch von Hugging Face | Unterhaltung **ohne** externen Server; komplett lokal im Add-on |
| **`needle`** | Needle 3 – schnell, nur Tool-Calls | Schnelle Gerätesteuerung, wenig RAM |
| **`openai`** | Beliebiger **OpenAI-kompatibler** Endpunkt mit Function-Calling | Unterhaltung + Steuerung über Ollama/llama.cpp-Server/LM Studio/OpenRouter/OpenAI |
| **`ha`** | Home Assists eigener Conversation-Agent | Volle HA-Intents/Aliase, kein eigenes Modell |

### Eingebauter llama.cpp-Backend

Im Add-on ist **llama.cpp** enthalten (`llama-cpp-python`, CPU). Beim ersten
Start eines Modells wird die GGUF-Datei **automatisch von Hugging Face** nach
`/data/models/llama` geladen; der Fortschritt steht in der Seitenleiste
(„Modell jetzt herunterladen“ startet den Download auch vorab).

Katalog (im UI als Dropdown, eigenes Repo/Datei-Paar eintragbar):

| Modell | Größe | Repo |
|---|---|---|
| **`qwen2.5-1.5b`** | ~1.0 GB | `Qwen/Qwen2.5-1.5B-Instruct-GGUF` |
| **`qwen3.5-0.8b`** | ~0.5 GB | `unsloth/Qwen3.5-0.8B-GGUF` |
| `qwen3-0.6b` | ~0.4 GB | `unsloth/Qwen3-0.6B-GGUF` |
| `qwen3-1.7b` | ~1.1 GB | `unsloth/Qwen3-1.7B-GGUF` |
| `llama-3.2-1b` / `3b` | 0.8 / 2.0 GB | `bartowski/Llama-3.2-…-Instruct-GGUF` |
| `phi-4-mini` | ~2.5 GB | `unsloth/Phi-4-mini-instruct-GGUF` |
| `qwen3-4b` | ~2.5 GB | `unsloth/Qwen3-4B-Instruct-2507-GGUF` |
| `gemma-3-1b` | ~0.8 GB | `ggml-org/gemma-3-1b-it-GGUF` (nur Chat) |

Einstellungen: Kontext (`llama_n_ctx`), Threads (`llama_threads`, 0 = auto),
GPU-Layers (`llama_gpu_layers`, 0 = CPU), Temperatur, Max. Tokens,
Thinking deaktivieren (Qwen3 → `/no_think`).

**Praxis:** Ein 0.6B/0.8B-Modell läuft auf einem Pi 5, ist aber bei Tool-Calls
unzuverlässiger (falsche Funktion oder Polarität). Dagegen greifen Grounding-
und Polaritäts-Prüfung plus HA-Fallback. **Qwen2.5 1.5B** ist der
ausgewogenste Default; für deutlich bessere Qualität `qwen3-4b` (auf Pi 5
langsam). Auf einem Pi ist ein externer `openai`-Backend (Ollama auf PC/NAS)
oft die bessere Wahl – beides ist umschaltbar.

**Hinweis zum Docker-Build:** `llama-cpp-python` wird aus dem Quellcode gebaut
(`build-essential` + `cmake`, danach entfernt). Der Build dauert auf einem Pi
einige Minuten. `GGML_NATIVE=OFF` hält die Binärdatei portabel.

Needle 3 erzeugt **keinen freien Text**. Für echte Unterhaltung ist der
`openai`-Backend gedacht; die Antworten kommen dann direkt vom LLM. Beim
`needle`-Backend werden Antworten aus den Tool-Ergebnissen gebaut
(Templates im UI überschreibbar).

### OpenAI-kompatibler Backend

Einfach Base-URL + Modell eintragen. Beispiele:

```text
Ollama (lokal):        http://localhost:11434/v1      (API-Key leer)
llama.cpp-Server:      http://localhost:8080/v1
LM Studio:             http://localhost:1234/v1
OpenRouter:            https://openrouter.ai/api/v1  (API-Key nötig)
OpenAI:                https://api.openai.com/v1      (API-Key nötig)
```

Empfohlene Modelle (im UI als Dropdown, eigenes Modell eintragbar):

| Modell | Größe | Anmerkung |
|---|---|---|
| `qwen3:1.7b` | ~1.4 GB | **Empfehlung** – gutes Tool-Calling, kompakt |
| `qwen3:0.6b` | ~0.5 GB | Pi-freundlich, schwächer |
| `qwen2.5:1.5b-instruct` | ~1 GB | solide, ausgereift |
| `llama3.2:1b` / `3b` | 0.8 / 2 GB | 3B deutlich besser |
| `gemma3:1b` | ~0.8 GB | klein |
| `phi4-mini` | ~2.5 GB | stark, größer |
| `qwen3:4b` | ~2.6 GB | beste Qualität der Liste |
| `functiongemma:270m` | winzig | nur Tool-Calling |

Mit Ollama z. B.:

```bash
ollama pull qwen3:1.7b
ollama serve          # lauscht auf :11434
```

Auf HAOS läuft Ollama als eigenes Add-on (Community) oder auf einem anderen
Rechner; die Base-URL zeigt dann auf dessen IP.

## Namensauflösung & Sicherheit

Zwei Dinge, die verhindern, dass Kommandos falsch ausgeführt werden:

- **Kanonische Enums + Aliase:** Im Decode-Grammar stehen nur die echten
  Entity-Namen. Aliase werden als Hinweis in den System-Prompt gegeben (und
  für die Auflösung genutzt). Grund: zwei ähnliche Enum-Werte wie
  „Schreibtischlampe" und „Schreibtisch" verwirren Needles Tool-Auswahl.
- **Grounding-Prüfung:** Wählt das Modell ein Gerät, das im Satz **nicht**
  vorkommt, wird der Call verworfen (z. B. „mach den schreibtisch an" →
  Modell rät „Kaffeemaschine"). Damit werden Fehlschaltungen verhindert.
- **Polaritäts-Prüfung:** Passt „an/aus" im Satz nicht zur gewählten Funktion,
  wird der Call verworfen (kleine Modelle wie Qwen3.5-0.8B wählen bei „aus"
  teils `turn_on`). Statt falsch zu schalten übernimmt der HA-Fallback.
- **HA-Fallback:** Kann das Backend nichts Sinnvolles liefern (kein Tool,
  nicht gegroundet, falsche Polarität, Fehler), übernimmt optional Home Assists
  eigener Agent – also genau das Verhalten, das bei „schalte schreibtischlampe
  ein" schon funktioniert hat.

## Einrichtung

1. Add-on installieren und starten. Beim ersten Start lädt der `needle`-Backend
   die Engine + Weights (~35 MB) von Hugging Face; der `openai`-Backend
   braucht nur den erreichbaren Endpunkt.
2. HA entdeckt den Dienst automatisch (Supervisor-Discovery).
3. **Einstellungen → Sprachassistenten → Assistant → Conversation** auf
   den Wyoming-Eintrag stellen.
4. Reihenfolge im Assistant: STT → Conversation → TTS.

## Konfiguration (Seitenleiste)

| Einstellung | Bedeutung |
|---|---|
| **Backend** | `needle` / `openai` / `ha` |
| **Dry-Run** | Aktionen nur anzeigen (Default: an) |
| **Domains** | Welche Domains als Tools angeboten werden |
| **Tools** | Tool-Auswahl per Checkbox (Default 5) |
| **Max. Tool-Schritte** | Mehrstufige Calls |
| **Sprache** | Sprache für HA/HA-Fallback |
| **System-Fakten** | z. B. `locale: de; device: homeassistant` |
| **Entity-Refresh** | Sekunden zwischen Entity-Aktualisierungen |
| **Verlauf behalten** | Anzahl Einträge |
| **Antwort-Templates** | JSON, nur für den `needle`-Backend |
| **HA-Agent als Fallback** | siehe oben (empfohlen) |
| **Grounding-Prüfung** | siehe oben (empfohlen) |
| **OpenAI Base-URL / API-Key / Modell / Temperatur / Max. Tokens** | nur `openai` |
| **GGUF-Modell / Kontext / Threads / GPU-Layers / Temperatur / Max. Tokens / Thinking** | nur `llama_cpp` |
| **HA-URL / HA-Token** | nur nötig außerhalb von HAOS |

### Tools

Domänenspezifische Namen (`turn_on_light`, `turn_off_switch`, …), weil das bei
deutschen Kommandos deutlich zuverlässiger ist als generische Tools.
**Wichtig:** Needle rendert **fünf oder weniger** Tools direkt; ab sechs greift
Tool-Retrieval und nicht ausgewählte Tools sind unerreichbar. Default daher:

```text
turn_on_light, turn_off_light, turn_on_switch, turn_off_switch, get_entity_state
```

Zusätzlich verfügbar: `set_media_volume`, `set_brightness`, `set_temperature`,
`activate_scene`. Bei mehr als 5 erscheint ein Hinweis im UI.

## Home-Assistant-Zugriff

Im Add-on über `homeassistant_api: true` und den Supervisor-Proxy
(`http://supervisor/core`) mit `SUPERVISOR_TOKEN` – **kein Long-Lived-Token
nötig**. Entities inkl. Namen, Bereichen und **Aliassen** kommen per REST +
WebSocket-Registry. Der HA-Fallback nutzt `POST /api/conversation/process`.

## Endpunkte

| Endpoint | Zweck |
|---|---|
| `GET /api/status` | HA-Status, Tools, Entities, Backends, Modell-Katalog, Stats |
| `GET/POST /api/settings` | Einstellungen |
| `POST /api/settings/reset` | auf Add-on-Optionen |
| `POST /api/refresh` | Entities neu laden |
| `POST /api/llama/download` | GGUF-Modell vorab herunterladen (Fortschritt im Status) |
| `POST /api/test` | Text direkt durch das Backend schicken |
| `GET/DELETE /api/history` | Verlauf (inkl. Backend/Fallback) |
| `GET /health` | Health-Check |

## Docker (ohne HAOS)

```bash
docker build -t needle-agent ./addons/needle_agent
docker run -d --name needle-agent -p 10300:10300 -p 8000:8000 \
  -v needle-data:/data \
  -e HA_URL=http://homeassistant.local:8123 \
  -e HA_TOKEN=<long-lived-token> \
  -e OPENAI_BASE_URL=http://host.docker.internal:11434/v1 \
  needle-agent
```
