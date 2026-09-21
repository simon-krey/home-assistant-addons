# Needle 3 Conversation

**Needle 3 als Conversation-Agent** für Home Assistant – über das
**Wyoming-Protokoll** (`handle`-Domain). Dadurch erscheint er nativ unter
**Einstellungen → Sprachassistenten → Assistant → Conversation** und lässt
sich mit der View Assist Companion App bzw. jeder Assist-Pipeline nutzen.

```text
View Assist / Assist
   ├── Sprache-zu-Text   → sherpa_stt (Wyoming asr)
   ├── Conversation      → needle3   (Wyoming handle)   ← dieses Add-on
   └── Text-zu-Sprache   → Piper     (Wyoming tts)
```

Needle liefert `function_calls`; das Add-on führt sie über die
Home-Assistant-API aus und baut daraus die Antwort (Needle erzeugt keinen
freien Text).

## Einrichtung

1. Add-on installieren und **starten**. Beim ersten Start lädt Needle 3 die
   Engine + `needle3.cact` (~35 MB) von Hugging Face nach `~/.cache` – danach
   läuft alles offline.
2. HA entdeckt den Dienst automatisch (Supervisor-Discovery). Prüfen unter
   **Einstellungen → Geräte & Dienste** (Wyoming Protocol).
3. **Einstellungen → Sprachassistenten → Assistant → Conversation** auf
   **needle3** stellen.
4. Fertig. Reihenfolge im Assistant: STT → Conversation → TTS.

Falls keine Discovery: Wyoming Protocol manuell mit Add-on-Hostname
(`local-needle_agent`) und Port `10300` hinzufügen.

## Konfiguration (Seitenleiste)

Alles ist im Ingress-Panel **Needle 3** einstellbar und wird in
`/data/settings.json` gespeichert:

| Einstellung | Bedeutung |
|---|---|
| **Dry-Run** | Aktionen nur anzeigen, nicht ausführen (Default: an) |
| **Domains** | Welche Domains als Tools angeboten werden (`light,switch,media_player,…`) |
| **Tools** | Auswahl der Tools per Checkbox (siehe unten) |
| **Max. Tool-Schritte** | Mehrstufige Calls (Default 4) |
| **Sprache** | Sprache, die HA angezeigt bekommt |
| **System-Fakten** | z. B. `locale: de; device: homeassistant` |
| **Entity-Refresh** | Sekunden zwischen Entity-Aktualisierungen |
| **Verlauf behalten** | Anzahl Einträge |
| **Antwort-Templates** | JSON, überschreibt die Standard-Antworten |
| **HA-URL / HA-Token** | nur nötig außerhalb von HAOS |
| **Debug-Logging** | ausführliche Logs |

### Tools

Bewusst **domänenspezifische** Tool-Namen (`turn_on_light`, `turn_off_switch`,
…). Tests mit deutschen Kommandos zeigen: das ist deutlich zuverlässiger als
generische `turn_on`/`on: bool`-Tools.

**Wichtig:** Needle rendert **fünf oder weniger** Tools direkt. Ab sechs greift
**Tool-Retrieval**, und nicht ausgewählte Tools sind dann unerreichbar – das
kann zu Fehlzuordnungen führen. Deshalb ist die Auswahl standardmäßig auf 5
begrenzt:

```text
turn_on_light, turn_off_light, turn_on_switch, turn_off_switch, get_entity_state
```

Verfügbar sind zusätzlich `set_media_volume`, `set_brightness`,
`set_temperature`, `activate_scene`. Wer mehr als 5 aktiviert, bekommt einen
Hinweis – funktioniert, kann aber ungenauer werden.

### Antworten

Needle erzeugt keinen freien Text, deshalb werden Antworten aus den
ausgeführten Calls gebaut. Defaults (im UI als JSON überschreibbar):

```json
{
  "turn_on_light": "{name} ist jetzt an.",
  "turn_off_light": "{name} ist jetzt aus.",
  "turn_on_switch": "{name} ist jetzt an.",
  "turn_off_switch": "{name} ist jetzt aus.",
  "set_brightness": "{name}: Helligkeit auf {volume} Prozent.",
  "set_media_volume": "{name}: Lautstaerke auf {volume}.",
  "set_temperature": "{name}: Temperatur auf {volume} Grad.",
  "get_entity_state": "{name}: {state}.",
  "refusal": "Das habe ich leider nicht verstanden.",
  "low_confidence": "Da bin ich mir nicht ganz sicher.",
  "error": "Beim Ausfuehren ist ein Fehler aufgetreten.",
  "dry_run_suffix": " (Testmodus)"
}
```

## Home-Assistant-Zugriff

Im Add-on über `homeassistant_api: true` und den Supervisor-Proxy
(`http://supervisor/core`) mit `SUPERVISOR_TOKEN` – **kein Long-Lived-Token
nötig**. Entities (inkl. Namen und Areas) werden per REST + WebSocket-Registry
geladen; Tool-Aufrufe laufen über `POST /api/services/<domain>/<service>`.

## Needle auf dem Pi

Needle 3 unterstützt `linux-arm64` und `linux-armv7`. Die Weights sind ~35 MB,
die Session-RAM klein. Die Basis hat 20 Layer und kann mit
`needle build --layers N` zugeschnitten werden. Ein Tool-Turn dauert je nach
Gerät grob 1–3 s.

## Endpunkte

| Endpoint | Zweck |
|---|---|
| `GET /api/status` | HA-Status, Tools, Entities, Stats |
| `GET/POST /api/settings` | Einstellungen |
| `POST /api/settings/reset` | auf Add-on-Optionen |
| `POST /api/refresh` | Entities neu laden |
| `POST /api/test` | Text direkt durch Needle schicken |
| `GET/DELETE /api/history` | Verlauf |
| `GET /health` | Health-Check |

## Docker (ohne HAOS)

```bash
docker build -t needle-agent ./addons/needle_agent
docker run -d --name needle-agent -p 10300:10300 -p 8000:8000 \
  -v needle-data:/data \
  -e HA_URL=http://homeassistant.local:8123 \
  -e HA_TOKEN=<long-lived-token> \
  needle-agent
```
