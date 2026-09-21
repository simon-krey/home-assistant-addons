# Sherpa STT (Wyoming)

Lokales **Streaming-Speech-to-Text** als **Wyoming-Server**. Dadurch erscheint
es in Home Assistant nativ unter **Einstellungen → Sprachassistenten →
Sprache-zu-Text** und lässt sich über die **View Assist Companion App** bzw.
jede Assist-Pipeline nutzen.

```text
View Assist / Assist
        │  (Wyoming: audio-start/chunk/stop)
        ▼
Sherpa STT Add-on  ──►  sherpa-onnx Streaming-Zipformer
        │
        ├── transcript  ──►  HA Assist (Intent → Aktion)
        └── Verlauf (Text + Audio-Sample)  ──►  Web-UI (Ingress)
```

## Einrichtung in Home Assistant

1. Add-on installieren und **starten**. Beim ersten Start wird das Modell
   (Standard: Deutsch/Kroko, ~70 MB) nach `/data/models` geladen.
2. HA entdeckt den Dienst automatisch: Das Add-on meldet sich beim Supervisor
   als Wyoming-Dienst an (`discovery: [wyoming]` im `config.yaml` ist die
   Allow-List, die Registrierung passiert beim Start automatisch).
   Prüfen unter **Einstellungen → Geräte & Dienste** – dort sollte ein
   **Wyoming Protocol**-Eintrag auftauchen (ggf. „Konfigurieren“ bestätigen).
3. **Einstellungen → Sprachassistenten → dein Assistant → Sprache-zu-Text**
   auf **sherpa-onnx** stellen.
4. Fertig. In der View Assist Companion App wird die STT dann über die
   Assist-Pipeline verwendet.

Falls keine automatische Discovery: **Wyoming Protocol** manuell hinzufügen,
Host = Add-on-Hostname (z. B. `local-sherpa_stt` bzw. die Container-IP),
Port = `10300`.

## Konfiguration

Alles ist **in der Seitenleiste** (Ingress-Panel **Sherpa STT**) einstellbar
und wird in `/data/settings.json` gespeichert:

| Einstellung | Bedeutung |
|---|---|
| **Modell** | Preset-Auswahl (siehe unten); `custom` für eigene URLs |
| **Eigene Modell-URL** | `.tar.bz2`-URL eines Streaming-Zipformer-Transducers (überschreibt die Preset-URL) |
| **Modell-Typ** | leer = sherpa-onnx erkennt automatisch; sonst z. B. `zipformer2` |
| **Sprache(n)** | Komma-getrennt, z. B. `de` oder `de,en`; leer = aus dem Preset |
| **Threads** | sherpa-onnx-CPU-Threads (1–8) |
| **Verlauf behalten** | Anzahl Einträge (0 = Verlauf aus) |
| **Zeroconf-Name** | optional; für Add-ons reicht die HA-Discovery |
| **Streaming-Transkripte** | sendet zusätzlich `transcript-start/chunk/stop` |
| **Audio-Samples speichern** | speichert zu jedem Transkript die Audiodatei |
| **Debug-Logging** | ausführliche Logs |

Beim ersten Start werden die **Add-on-Optionen** (`config.yaml`) als
Startwerte übernommen. Danach ist die Web-UI maßgeblich; mit
**„Auf Add-on-Optionen zurücksetzen“** lässt sich das zurücksetzen.

## Unterstützte Modelle

Das Add-on kennt zwei Engine-Arten:

- **Streaming** (`OnlineRecognizer.from_transducer`): liefert Partials während
  des Sprechens, niedrige Latenz.
- **Offline** (`OfflineRecognizer`): dekodiert erst nach `audio-stop`; dafür
  oft bessere Qualität (Whisper/Canary). `streaming_transcripts` wird dabei
  ignoriert.

### Streaming-Zipformer (Transducer)

Unterstützt werden **Streaming-Zipformer-Transducer** mit `encoder*.onnx`,
`decoder*.onnx`, `joiner*.onnx`, `tokens.txt`. Die **`*-ctc-*`-Streaming-Modelle
funktionieren nicht** (kein Transducer). `model_type` bleibt standardmäßig leer
(sherpa-onnx erkennt die Architektur automatisch, genau wie das offizielle
`wyoming-faster-whisper`).

| Preset | Sprache | Archiv | Anmerkung |
|---|---|---|---|
| `de` | Deutsch | 58 MB | **Kroko** – beste deutsche Streaming-Qualität, Default |
| `en-kroko` | Englisch | 57 MB | Kroko, Groß-/Kleinschreibung + Satzzeichen |
| `en-20M` | Englisch | 128 MB | klein, älter, schwächer |
| `es-kroko` / `fr-kroko` | Spanisch / Französisch | 124 / 57 MB | Kroko |
| `multi-8` | ar/en/id/ja/ru/th/vi/zh | 259 MB | ein Modell, viele Sprachen |
| `zh-en` | Chinesisch+Englisch | 458 MB | bilingual |
| `zh-int8` / `zh-multi-int8` | Chinesisch | 133 / 62 MB | |
| `ru-int8` | Russisch | 24 MB | Vosk small |
| `bn` | Bengali | 87 MB | Vosk |
| `ko` | Koreanisch | 418 MB | |

### Offline (Whisper, NeMo Canary)

| Preset | Sprache | Archiv | Pi-Tauglichkeit (x86 gemessen) |
|---|---|---|---|
| `whisper-tiny-int8` | 99 (Whisper) | 116 MB | läuft ✅ |
| `whisper-base-int8` | 99 | 208 MB | läuft ✅ |
| `whisper-small-int8` | 99 | 639 MB | Pi 5 ⚠️ / Pi 4 ❌ |
| `canary-180m-flash-int8` | en/es/**de**/fr | 154 MB | läuft ✅, sehr schnell |

Lokal gemessen (x86, 4 Threads, deutscher Test-Satz):

```text
canary-180m-flash-int8   RTF 0.10   'Alles hat ein Ende, nur die Wurst hat zwei.'
whisper-small-int8       RTF 0.62   'Alles hat ein Ende, nur die Wurst hat zwei.'
de (Kroko, streaming)    RTF 0.02   'Alles hat ein Ende, nur die Wurst hat'
```

Canary liefert hier **mit Satzzeichen** und ist deutlich schneller als Whisper
small. Beide Offline-Modelle haben keine Partials → die Antwort kommt erst nach
Sprechende. Für Deutsch ist Canary damit oft die bessere Offline-Wahl.

Für **Deutsch** gibt es unter den Streaming-Zipformern nur Kroko. Größere
Archive (`zh-xlarge`, 600 MB–1,3 GB) sind für den Pi nicht sinnvoll.

### Eigenes Modell eintragen

1. `model` = `custom`
2. `model_url` = `.tar.bz2`-URL (Zielordner wird aus dem Archivnamen abgeleitet)
3. `kind` = `streaming` / `whisper` / `canary`
4. optional `model_type` (leer = auto) und `language` (kommagetrennt)

## Verlauf

Jede Erkennung wird mit Zeitstempel, Text, Dauer, RTF, Quelle und – falls
aktiviert – **Audio-Sample** gespeichert. Im Verlauf der Web-UI gibt es dazu
einen eingebauten **Audio-Player**, sodass man Transkript und Aufnahme direkt
vergleichen kann.

Ablage: `/data/history/index.json` und `/data/history/audio/<id>.wav`
(16 kHz, 16-bit, mono). Über `backup_exclude` sind Modelle und Verlauf von
Backups ausgenommen.

## Wyoming-Details

- **Programm:** `sherpa-onnx`
- **Ablauf:** `describe` → `info`; `transcribe` → `audio-start` →
  `audio-chunk`* → `audio-stop` → `transcript`
- Audio wird intern auf **16 kHz / 16-bit / mono** konvertiert
  (`AudioChunkConverter`), unabhängig davon, was der Client sendet.
- `requires_external_vad = true`: Die Endpunkt-Erkennung (wann der Nutzer
  fertig gesprochen hat) übernimmt Home Assistant bzw. die Companion App.
- Optional werden Streaming-Transkripte gesendet
  (`transcript-start` / `transcript-chunk` / `transcript-stop`), gefolgt vom
  regulären `transcript`-Event für Abwärtskompatibilität.

## Endpunkte der Web-UI

| Endpoint | Zweck |
|---|---|
| `GET /api/status` | Engine, Einstellungen, Statistiken |
| `GET/POST /api/settings` | Einstellungen lesen/schreiben |
| `POST /api/settings/reset` | auf Add-on-Optionen zurücksetzen |
| `GET /api/history` | Verlauf |
| `GET /api/history/{id}/audio` | Audio-Sample (WAV) |
| `DELETE /api/history[/{id}]` | Verlauf löschen |
| `GET /health` | Health-Check |

## Docker (ohne HAOS)

```bash
docker build -t sherpa-stt ./addons/sherpa_stt
docker run -d --name sherpa-stt \
  -p 10300:10300 -p 8000:8000 \
  -v sherpa-stt-data:/data \
  -e STT_MODEL=de \
  sherpa-stt
```

Dann in HA das Wyoming-Protokoll manuell mit `<host>:10300` hinzufügen.
