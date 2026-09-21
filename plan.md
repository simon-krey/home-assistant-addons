# PC-Prototyp: Streaming-STT → Needle 3 → Terminal

## 1. Ziel

Ein lokales Python-Programm auf dem PC soll einen kontinuierlichen Mikrofon-Stream verarbeiten und daraus strukturierte Tool-Calls erzeugen.

Die Pipeline lautet:

```text
Mikrofon
   │
   ▼
Audio Capture
   │
   ▼
Streaming Zipformer
   │
   ├── Partial Transcript
   │
   └── Final Transcript
            │
            ▼
         Needle 3
            │
            ▼
      Function Calls
            │
            ▼
        Terminal
```

In der ersten Version werden **keine echten Tools ausgeführt**.

Needle 3 soll lediglich zeigen, welchen Tool-Call es erkannt hat.

---

# 2. Ziel der ersten Version

Beispiel:

```text
Benutzer:
"mach das licht im wohnzimmer an"
```

Terminal:

```text
[STT][partial] mach das licht
[STT][partial] mach das licht im wohnzimmer
[STT][final]   mach das licht im wohnzimmer an

[NEEDLE]
type: call
confidence: 0.94

function_calls:
[
  {
    "name": "set_light",
    "arguments": {
      "room": "wohnzimmer",
      "state": true
    }
  }
]
```

Zusätzlich sollen die von Needle gelieferten Debug-Metriken angezeigt werden, etwa:

```text
prefill_tps
decode_tps
peak_ram_mb
confidence
```

Diese Werte liefert die aktuelle Needle-3-API direkt in der Response.

---

# 3. Technologiestack

## Python

Aktuelle Python-Version des Systems verwenden, vorzugsweise Python 3.11 oder neuer.

## Audio

Für den PC:

```text
sounddevice
numpy
```

`sounddevice` übernimmt zunächst nur die Mikrofonaufnahme.

## STT

```text
sherpa-onnx
```

mit einem **Streaming-Zipformer**.

sherpa-onnx besitzt eine echte Streaming-ASR-Schnittstelle: Audiosamples werden verarbeitet, während sie eintreffen, und die offiziellen Beispiele zeigen genau diesen kontinuierlichen Mikrofonbetrieb.

## Tool Calling

```text
cactus-needle
```

Installation laut aktueller Needle-Dokumentation:

```bash
pip install cactus-needle
```

Die Python-API stellt `needle.Needle(...)` sowie `agent.complete(...)` bereit.

## Debug-Ausgabe

Zunächst reicht:

```text
print()
json.dumps()
```

Später optional:

```text
rich
```

für eine schönere Terminal-Oberfläche.

---

# 4. Projektstruktur

```text
voice-agent/
│
├── main.py
├── config.py
│
├── audio.py
├── stt.py
├── needle_agent.py
├── tools.py
│
├── models/
│   └── zipformer/
│
├── requirements.txt
│
└── README.md
```

---

# 5. Verantwortlichkeiten

## `audio.py`

Nur für Audioaufnahme zuständig.

Aufgabe:

```text
Mikrofon
   ↓
PCM Samples
   ↓
Queue
```

Das Audio-Modul soll nichts über STT oder Needle wissen.

Interface ungefähr:

```python
class AudioCapture:
    def start(self):
        ...

    def stop(self):
        ...

    def get_audio(self):
        ...
```

---

# 6. `stt.py`

Dieses Modul kapselt sherpa-onnx.

Es erstellt einen Streaming-Recognizer und hält genau einen laufenden Recognition-Stream.

Aufgabe:

```text
PCM chunk
   ↓
Zipformer
   ↓
partial transcript
```

und bei erkanntem Endpoint:

```text
final transcript
```

sherpa-onnx besitzt dafür Online-Recognizer und Endpoint-Detection für Streaming-Modelle.

Interface:

```python
class StreamingSTT:

    def accept_audio(self, samples):
        ...

    def get_result(self):
        ...

    def is_endpoint(self):
        ...

    def reset(self):
        ...
```

---

# 7. Partial vs. Final

Das ist eine zentrale Trennung.

Während jemand spricht:

```text
[partial] mach
[partial] mach das
[partial] mach das licht
[partial] mach das licht im
[partial] mach das licht im wohnzimmer
```

Diese Ergebnisse werden nur zu Debug-Zwecken angezeigt.

Needle bekommt sie **nicht**.

Erst wenn Zipformer einen Endpoint erkennt:

```text
[final] mach das licht im wohnzimmer an
```

wird Needle aufgerufen.

Damit wird verhindert, dass Needle für jedes einzelne Partial-Update eine neue Inferenz durchführen muss.

---

# 8. `tools.py`

Hier werden zunächst Dummy-Tools definiert.

Beispiel:

```python
import needle


@needle.tool
def set_light(room: str, state: bool):
    """Turn a light on or off in a room."""
    pass
```

Wichtig:

Die Funktion soll in dieser Phase **keine echte Aktion ausführen**.

Sie existiert nur, damit Needle ihr Schema kennt.

Needle leitet aus Funktionssignatur und Beschreibung das Tool-Schema ab.

---

# 9. `needle_agent.py`

Hier wird Needle 3 initialisiert.

Beispielkonzept:

```python
import needle

from tools import set_light

agent = needle.Needle(
    tools=[set_light]
)
```

Die wichtigste Methode für den Debug-Prototyp ist:

```python
agent.complete(text)
```

und **nicht**:

```python
agent.run(text)
```

Der Grund:

`run()` führt die Tools selbst aus und ist für einen späteren Agenten-Loop gedacht.

`complete()` liefert dagegen den einzelnen rohen Needle-Schritt zurück.

---

# 10. Needle-Response

Die Anwendung soll die Response unverändert analysieren.

Beispiel:

```python
response = agent.complete(text)
```

Interessante Felder:

```python
response["type"]
response["success"]
response["function_calls"]
response["reasoning"]
response["confidence"]
response["prefill_tps"]
response["decode_tps"]
response["peak_ram_mb"]
```

Die aktuelle Needle-Dokumentation beschreibt diese Response-Struktur explizit.

---

# 11. Terminal-Ausgabe

Die Debug-Ausgabe sollte ungefähr so aussehen:

```text
────────────────────────────────────────
AUDIO
────────────────────────────────────────
Device: USB Microphone
Sample rate: 16000
Channels: 1

────────────────────────────────────────
STT
────────────────────────────────────────
[partial] mach das
[partial] mach das licht
[partial] mach das licht im wohnzimmer
[final]   mach das licht im wohnzimmer an

────────────────────────────────────────
NEEDLE 3
────────────────────────────────────────
type: call
success: true
confidence: 0.94

reasoning:
'living room' -> room
'turn on' -> state=true

function_calls:
[
    {
        "name": "set_light",
        "arguments": {
            "room": "wohnzimmer",
            "state": true
        }
    }
]

prefill_tps: 4300
decode_tps: 850
peak_ram_mb: 28.5
────────────────────────────────────────
```

Needle 3 liefert `function_calls` als strukturierte Daten und garantiert die Tool-Argumente gegen das deklarierte Schema.

---

# 12. Pipeline-Architektur

Für die erste Version sollte die Pipeline **event-basiert** sein.

```text
                ┌───────────────┐
                │   Microphone  │
                └───────┬───────┘
                        │
                        ▼
                ┌───────────────┐
                │ AudioCapture   │
                └───────┬───────┘
                        │
                        ▼
                  audio_queue
                        │
                        ▼
                ┌───────────────┐
                │  Zipformer     │
                │ Streaming STT  │
                └───────┬───────┘
                        │
             ┌──────────┴──────────┐
             │                     │
             ▼                     ▼
         partial                final
             │                     │
             ▼                     ▼
         Terminal              text_queue
                                   │
                                   ▼
                             ┌───────────┐
                             │ Needle 3  │
                             └─────┬─────┘
                                   │
                                   ▼
                             tool_call
                                   │
                                   ▼
                              Terminal
```

---

# 13. Threads / Async

Für den Anfang sind drei Worker ausreichend:

```text
Audio Worker
STT Worker
Needle Worker
```

### Audio Worker

Liest das Mikrofon und schreibt Audio-Chunks:

```python
audio_queue.put(samples)
```

### STT Worker

Liest:

```python
samples = audio_queue.get()
```

und gibt Partial/Final-Transkripte aus.

Bei einem finalen Ergebnis:

```python
text_queue.put(final_text)
```

### Needle Worker

Liest:

```python
text = text_queue.get()
```

und macht:

```python
response = agent.complete(text)
```

Anschließend wird die vollständige Response ausgegeben.

---

# 14. Keine direkte Kopplung

Die Module sollen nicht gegenseitig Funktionen aufrufen.

Also nicht:

```text
stt.py
    ↓
needle_agent.py
```

sondern:

```text
stt.py → Queue → main.py → Queue → needle_agent.py
```

Dadurch kann später problemlos geändert werden:

```text
Mikrofon
```

zu:

```text
WebSocket
```

oder:

```text
UDP
```

ohne Needle ändern zu müssen.

---

# 15. Audioquelle abstrahieren

Obwohl zuerst das Mikrofon verwendet wird, sollte die Audioquelle ein eigenes Interface haben.

```python
class AudioSource:
    def read(self):
        ...
```

Dann können später mehrere Quellen implementiert werden:

```text
MicrophoneSource
FileSource
WebSocketSource
PipeSource
```

Für den ersten Test:

```text
MicrophoneSource
```

Später kann damit ein echter Audio-Stream eingespeist werden.

---

# 16. Speech Endpoint

Der Ablauf soll sein:

```text
START SPEECH
     │
     ▼
Zipformer läuft
     │
     ├── partial
     ├── partial
     ├── partial
     └── partial
             │
             ▼
       SILENCE DETECTED
             │
             ▼
       final transcript
             │
             ▼
          Needle 3
```

Nach dem Endpoint wird der Streaming-State für die nächste Äußerung zurückgesetzt.

---

# 17. Fehlerfälle

Diese Fälle müssen explizit behandelt werden.

### Kein Tool passend

Needle kann mit leerem `function_calls` antworten.

Das soll lediglich ausgegeben werden:

```text
[NEEDLE]
No tool call
```

Ein fehlendes passendes Tool ist laut Needle-Verhaltensvertrag kein Grund für einen freien Text-Fallback.

### Niedrige Confidence

```text
confidence < threshold
```

In dieser Phase:

```text
[NEEDLE] LOW CONFIDENCE
```

und nicht ausführen.

Needle liefert dafür eine kalibrierte `confidence`-Angabe.

### Needle Error

Response vollständig ausgeben:

```text
success
error
error_code
```

---

# 18. Debug-Modus

Eine globale Option:

```text
--debug
```

soll aktiviert werden können.

Beispiel:

```bash
python main.py --debug
```

Dann zusätzlich:

```text
Audio chunk duration
STT processing time
STT partial latency
STT final latency
Needle latency
Needle confidence
Needle RAM
Needle TPS
```

ausgeben.

---

# 19. Latenzmessung

Für jede Äußerung werden Zeitstempel gespeichert:

```text
t_audio
t_first_partial
t_final
t_needle_start
t_needle_end
```

Daraus:

```text
Audio → first partial
Audio → final transcript
Final transcript → Needle start
Needle inference
End-to-end latency
```

Beispiel:

```text
STT first partial:   142 ms
STT final:           287 ms
Needle inference:     41 ms
End-to-end:          328 ms
```

Damit lässt sich später objektiv feststellen, welcher Teil der Pipeline die Latenz verursacht.

---

# 20. Modellkonfiguration

Die Modellpfade sollen nicht fest im Code stehen.

`config.py`:

```python
STT_MODEL_DIR = "models/zipformer"

SAMPLE_RATE = 16000
CHANNELS = 1

NEEDLE_MAX_NEW_TOKENS = 256
NEEDLE_CONFIDENCE_THRESHOLD = 0.7
```

Später kann dies über CLI-Argumente oder eine YAML/TOML-Datei ersetzt werden.

---

# 21. Requirements

Erste Fassung:

```text
numpy
sounddevice
sherpa-onnx
cactus-needle
```

Optional:

```text
rich
```

---

# 22. Entwicklungsreihenfolge

## Phase 1: Audio

```text
Mikrofon
→
Python
→
Terminal
```

Ziel:

Kontinuierliche PCM-Samples kommen zuverlässig an.

---

## Phase 2: Zipformer

```text
Mikrofon
→
Zipformer
→
Terminal
```

Ziel:

Partial und Final Transcripts funktionieren.

---

## Phase 3: Needle separat

Test ohne Mikrofon:

```text
Hardcoded Text
→
Needle 3
→
Terminal
```

Beispiel:

```python
agent.complete("mach das licht im wohnzimmer an")
```

Damit wird sichergestellt, dass Needle und die Tool-Schemas korrekt funktionieren.

---

## Phase 4: Verbindung

```text
Mikrofon
→
Zipformer
→
Final transcript
→
Needle
→
Terminal
```

Das ist der erste vollständige Prototyp.

---

## Phase 5: Debugging

Latenzen und Metriken hinzufügen.

```text
STT latency
Needle latency
Confidence
RAM
TPS
```

---

## Phase 6: Tool Dispatcher

Erst jetzt:

```text
Needle
   ↓
Tool Dispatcher
   ↓
Dummy execution
```

Beispiel:

```text
[DISPATCH]
Would execute:

set_light(
    room="wohnzimmer",
    state=True
)
```

Noch keine reale Hausautomation.

---

# 23. Spätere Erweiterungen

Nach dem funktionierenden PC-Prototyp können folgende Komponenten ergänzt werden:

```text
Wake Word
VAD
echte Tools
Tool results
Needle multi-turn context
TTS
WebSocket Audio
GUI
Raspberry Pi Port
```

Die Reihenfolge bleibt:

```text
STT
 ↓
Needle
 ↓
Tool
 ↓
Result
 ↓
Needle
```

Needle unterstützt bereits einen mehrstufigen `complete()`-Loop, bei dem das Ergebnis eines ausgeführten Tools als nächste Eingabe zurückgegeben wird.

---

# 24. Endziel

Die fertige Architektur soll letztlich so aussehen:

```text
                    ┌───────────────┐
                    │    AUDIO      │
                    │   STREAM      │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  STREAMING    │
                    │   ZIPFORMER   │
                    └───────┬───────┘
                            │
                      final transcript
                            │
                            ▼
                    ┌───────────────┐
                    │    NEEDLE 3   │
                    │ TOOL CALLING  │
                    └───────┬───────┘
                            │
                       function_calls
                            │
                            ▼
                    ┌───────────────┐
                    │ TOOL DISPATCH │
                    └───────┬───────┘
                            │
               ┌────────────┴────────────┐
               │                         │
               ▼                         ▼
          Real Tool                 Debug Terminal
```

Der **PC-Prototyp endet bewusst beim Terminal**. Erst wenn diese gesamte Pipeline stabil funktioniert, wird die Audioquelle, Performance und Packaging-Architektur für den Raspberry Pi angepasst.

---

# 25. Wichtiger technischer Punkt

Needle 3 muss nicht selbst den Audio-Stream verarbeiten. Die aktuelle API kann zwar auch Audio direkt über `complete(..., audio=...)` entgegennehmen, inklusive PCM16/Float32-Input, aber für diesen Prototyp wird bewusst der Weg

```text
Audio → Zipformer → Text → Needle
```

verwendet.

Das hält STT und Tool-Calling sauber getrennt und macht die spätere Audioquelle austauschbar.

