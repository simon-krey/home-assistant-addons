# Sherpa STT (Wyoming)

Lokales **Speech-to-Text** als **Wyoming-Server**: **Kroko** (Streaming, mit
Zwischenergebnissen), **NVIDIA Parakeet TDT 0.6B v3** (Offline, mit
Satzzeichen), **NVIDIA Nemotron 3.5 ASR 0.6B** (Streaming, mehrsprachig) und
**OpenAI Whisper** in allen Größen (Offline). Dadurch erscheint es in Home
Assistant nativ unter **Einstellungen → Sprachassistenten → Sprache-zu-Text**
und lässt sich über die **View Assist Companion App** bzw. jede Assist-Pipeline
nutzen.

```text
View Assist / Assist
        │  (Wyoming: audio-start/chunk/stop)
        ▼
Sherpa STT Add-on  ──►  sherpa-onnx (Kroko | Parakeet v3 | Nemotron 3.5 | Whisper)
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
| **Eigene Modell-URL** | `.tar.bz2`-URL (Kroko-Transducer oder NeMo-Parakeet; überschreibt die Preset-URL) |
| **Modell-Typ** | leer = sherpa-onnx erkennt automatisch; sonst z. B. `zipformer2` oder `nemo_transducer` |
| **Sprache(n)** | Komma-getrennt, z. B. `de` oder `de,en`; leer = aus dem Preset |
| **Threads** | sherpa-onnx-CPU-Threads (1–8) |
| **Verlauf behalten** | Anzahl Einträge (0 = Verlauf aus) |
| **Zeroconf-Name** | optional; für Add-ons reicht die HA-Discovery |
| **Audio-Vorverarbeitung** | `off` / `light` / `normalize` / `full` (siehe unten) |
| **Hotwords** | eigene Wortliste + optional Entity-Namen/Aliase aus HA (siehe unten; nur Parakeet) |
| **Biasing-Stärke** | 0,5–10 (Standard 2,5) |
| **Streaming-Transkripte** | sendet zusätzlich `transcript-start/chunk/stop` |
| **Audio-Samples speichern** | speichert zu jedem Transkript die Audiodatei |
| **Debug-Logging** | ausführliche Logs |

Beim ersten Start werden die **Add-on-Optionen** (`config.yaml`) als
Startwerte übernommen. Danach ist die Web-UI maßgeblich; mit
**„Auf Add-on-Optionen zurücksetzen“** lässt sich das zurücksetzen.

Wird eine Option **im HA-Konfigurationstab** geändert, schreibt der Supervisor
`/data/options.json` und startet das Add-on neu. Solche Änderungen werden
erkannt und übernommen – aber **nur die tatsächlich geänderten Felder**, damit
die Web-UI-Einstellungen erhalten bleiben. Umgekehrt überschreiben
Web-UI-Änderungen die Add-on-Optionen nicht dauerhaft.

## Unterstützte Modelle

Das Add-on kennt drei Engine-Arten:

- **Streaming** (`OnlineRecognizer.from_transducer`): liefert Partials während
  des Sprechens, niedrige Latenz.
- **Offline** (`OfflineRecognizer.from_transducer`, NeMo-Transducer): dekodiert
  erst nach `audio-stop`, liefert dafür Satzzeichen. `streaming_transcripts`
  wird dabei ignoriert.
- **Nemotron 3.5** (Streaming-Transducer, `model_type="nemotron"`,
  `feature_dim=128`): mehrsprachiges Streaming mit Sprach-Prompt.

### Streaming – Kroko (Transducer)

Unterstützt werden **Streaming-Zipformer-Transducer** mit `encoder*.onnx`,
`decoder*.onnx`, `joiner*.onnx`, `tokens.txt`. Die **`*-ctc-*`-Streaming-Modelle
funktionieren nicht** (kein Transducer). `model_type` bleibt standardmäßig leer
(sherpa-onnx erkennt die Architektur automatisch).

| Preset | Sprache | Archiv | Anmerkung |
|---|---|---|---|
| `de` | Deutsch | 58 MB | **Kroko** – beste deutsche Streaming-Qualität, Default |
| `en-kroko` | Englisch | 57 MB | Kroko, Groß-/Kleinschreibung + Satzzeichen |
| `es-kroko` / `fr-kroko` | Spanisch / Französisch | 124 / 57 MB | Kroko |

### Offline – NVIDIA Parakeet TDT 0.6B v3

| Preset | Sprachen | Archiv | Anmerkung |
|---|---|---|---|
| `parakeet-v3` | 25 EU-Sprachen (u. a. de, en, es, fr, it, nl, pt) | ~640 MB | liefert Satzzeichen, sehr schnell auf x86 und Pi 5 |

### Streaming – NVIDIA Nemotron 3.5 ASR 0.6B

Mehrsprachiges **Streaming**-Modell (Cache-Aware FastConformer-RNNT) mit
Sprach-Prompt und 28 nutzbaren Sprachen (u. a. de, en, es, fr, it, pt, nl, tr,
ru, ar, hi, ja, ko, vi, uk). Zwei Chunk-Größen:

| Preset | Chunk | Archiv | Anmerkung |
|---|---|---|---|
| `nemotron-v3` | 560 ms | ~650 MB | guter Kompromiss aus Latenz und Genauigkeit |
| `nemotron-v3-hq` | 1120 ms | ~650 MB | größerer Kontext, genauer, etwas höhere Latenz |

Die **Sprache** aus den Einstellungen wird als Prompt pro Stream gesetzt
(`de`, `en`, … oder `auto` für automatische Erkennung). Ohne Angabe nutzt das
Add-on die erste Preset-Sprache.

Lokal gemessen (x86, 2 Threads, deutscher Test-Satz):

```text
parakeet-v3              RTF 0.077  'Alles hat ein Ende, nur die Wurst hat zwei.'
nemotron-v3 (560 ms)     RTF 0.16   'Alles hat ein Ende, nur die Wurst hat zwei.'
de (Kroko, streaming)    RTF 0.025  'Alles hat ein Ende, nur die Wurst hat'
```

Kroko bleibt der Default, weil es am kleinsten und schnellsten ist und
Zwischenergebnisse liefert. Parakeet v3 (Offline, Satzzeichen, Contextual
Biasing) und Nemotron 3.5 (Streaming, mehrsprachig) sind die Alternativen.
Über **„Selbsttest“** in der Übersicht lässt sich die Test-WAV des geladenen
Modells dekodieren (Text, RTF, Echtzeit-Fähigkeit).

### Offline – OpenAI Whisper

Alle gängigen Whisper-Größen als ONNX (sherpa-onnx, int8 – also edge-tauglich):

| Preset | Parameter | Sprachen | Anmerkung |
|---|---|---|---|
| `whisper-tiny` / `whisper-tiny.en` | 39M | 99 / nur en | schnell, geringste Qualität |
| `whisper-base` | 74M | 99 | Kompromiss |
| `whisper-small` | 244M | 99 | gute Qualität, Pi 5 ok |
| `whisper-medium` | 769M | 99 | Pi 5 sehr langsam |
| `whisper-large-v3` | ~1,5G | 99 | nur x86/GPU sinnvoll |
| `whisper-turbo` | 809M | 99 | schnell + genau |

Whisper liefert Satzzeichen und Groß-/Kleinschreibung. Die **Sprache** aus den
Einstellungen wird als Decoder-Prompt gesetzt (`de`, `en`, …). Die mitgelieferte
Test-WAV ist Englisch – der Selbsttest dekodiert sie deshalb immer mit `en`.

> **Hinweis zu `edge_whisper`:** Das Projekt
> [ktomanek/edge_whisper](https://github.com/ktomanek/edge_whisper) optimiert
> Whisper für Edge-Geräte (10-s-Encoder, int8, optional Hailo-NPU). Es liefert
> **keine fertigen Modelle**, sondern Konvertierungsskripte, und sein
> Optimum-ONNX-Decoder (KV-Cache) ist nicht mit sherpa-onnx kompatibel. Dieses
> Add-on nutzt deshalb die sherpa-onnx-Whisper-Modelle (int8, ONNX-Runtime auf
> der CPU) – derselbe Edge-Ansatz, nur direkt lauffähig. Wer die Hailo-NPU
> nutzen will, betreibt `edge_whisper` separat.

### Eigenes Modell eintragen

1. `model` = `custom`
2. `model_url` = `.tar.bz2`-URL (Zielordner wird aus dem Archivnamen abgeleitet)
3. `kind` = `streaming`, `parakeet`, `nemotron` oder `whisper`
4. optional `model_type` (leer = auto) und `language` (kommagetrennt)

## Audio-Vorverarbeitung

Die Modelle erwarten Sprache mit gleichmäßigem Pegel. In der Praxis kommen
Aufnahmen aber mit DC-Anteil, Brummen/Dröhnen, sehr leisem oder übersteuertem
Pegel und Grundrauschen an. Das Add-on verarbeitet den Ton deshalb **vor** der
Erkennung – zustandsbehaftet, also streaming-fähig:

| Modus | Was passiert |
|---|---|
| `off` | Rohsignal (keine Veränderung) |
| `light` | DC-Entfernung + 80 Hz-Hochpass + sanfter Limiter |
| `normalize` | `light` + AGC/Pegelnormalisierung (Standard) |
| `full` | `normalize` + leises Rauschgate in Sprechpausen |

Die Verfahren sind bewusst **konservativ und sprachschonend**. Aggressive
Rauschunterdrückung (spektrale Subtraktion o. Ä.) ist **nicht** enthalten, weil
sie die Erkennung laut mehreren Studien (Deepgram „Noise Reduction Paradox“,
medizinische ASR-Studien 2024–2026) sogar verschlechtern kann: sie entfernt
Sprachmerkmale, die das Modell braucht.

Lokale Messungen (deutscher Test-Satz, x86):

```text
Szenario                     ohne Vorverarbeitung   mit Vorverarbeitung
Clipping/Übersteuerung       WER 0,11               WER 0,00  (light)
leise + Brummen + Rauschen   WER 0,89               WER 0,56  (normalize)
sauberes Audio               WER 0,11               WER 0,11  (neutral)
```

Vorverarbeitung hilft also bei schlechten Aufnahmen und ist bei sauberem Audio
neutral. Der Modus ist in der Seitenleiste umstellbar und wird beim Selbsttest
mit angewendet.

## Hotwords / Contextual Biasing

Seltene Eigennamen („Schreibtischlampe") erkennt das Modell schlecht. Mit
**Contextual Biasing** wird der Decoder auf eine Wortliste gelenkt:

- **Parakeet v3** (Offline-Transducer) unterstützt das und profitiert deutlich –
  im Test wurde bei starkem Rauschen aus „Halte sie schreibt die Schlampe an."
  wieder „Schalte Schreibtischlampe an.".
- **Kroko** (Streaming) liefert keine passende `bpe.vocab` mit; Tests zeigen dort
  praktisch keinen Effekt. Biasing ist deshalb **nur bei Parakeet** aktiv.

Die Hotwords kommen aus zwei Quellen:

1. **manuell** (Komma-/Zeilen-getrennte Liste im UI),
2. **Home Assistant**: Entity-Namen **und Aliase** der gewählten Domains
   (im Add-on über den Supervisor, sonst über `ha_url` + `ha_token`).

Die nötige `bpe.vocab` wird automatisch aus der `tokens.txt` des Modells erzeugt.
Die **Biasing-Stärke** ist einstellbar (Standard 2,5): 2–4 hilft gut, zu hohe
Werte können die Erkennung verfälschen. Im UI gibt es „Hotwords jetzt aus HA
laden"; der Status zeigt die aktive Anzahl. Technisch läuft das über
`modified_beam_search` (rund 10–15 % langsamer als greedy, weiter klar
echtzeitfähig).

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
| `GET /api/selftest` | Test-WAV des Modells dekodieren (Text, RTF) |
| `GET /api/hotwords` | aktive Hotwords |
| `POST /api/hotwords/refresh` | Hotwords neu aus HA laden + Engine neu bauen |
| `GET/POST /api/settings` | Einstellungen lesen/schreiben |
| `POST /api/settings/reset` | auf Add-on-Optionen zurücksetzen |
| `GET /api/history` | Verlauf |
| `GET /api/history/{id}/audio` | Audio-Sample (WAV) |
| `DELETE /api/history[/{id}]` | Verlauf löschen |
| `GET /health` | Health-Check |

## Web-UI

Die Oberfläche ist in drei Bereiche gegliedert:

- **Übersicht** – Status, Selbsttest und die Anleitung zum Einbinden in HA.
- **Einstellungen** – Modell, Betrieb und Logging. Es werden **nur geänderte
  Felder** gespeichert, sodass ein Speichern keine übrigen Werte überschreibt.
- **Verlauf** – alle Erkennungen mit Audio-Player.

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
