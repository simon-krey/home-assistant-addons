# Home-Assistant-Integration für die Voice-Pipeline

## 1. Ziel

Die bestehende Pipeline

```text
Mikrofon
   ↓
Streaming Zipformer
   ↓
Final Transcript
   ↓
Needle 3
   ↓
Function Call
   ↓
Terminal
```

wird um Home Assistant erweitert:

```text
Mikrofon
   ↓
Streaming Zipformer
   ↓
Final Transcript
   ↓
Needle 3
   ↓
Tool Call
   ↓
Home Assistant Adapter
   ↓
Home Assistant
```

Der PC ist die erste Plattform.

Danach soll **derselbe Python-Code** möglichst unverändert auf dem Raspberry Pi laufen.

Nur folgende Dinge sollen plattformabhängig sein:

```text
Audio-Backend
Modellpfade
Konfiguration
```

Die Home-Assistant-Anbindung selbst soll identisch bleiben.

---

# 2. Grundprinzip

Needle 3 soll **nicht direkt mit Home Assistant kommunizieren**.

Stattdessen:

```text
Needle 3
   ↓
Python Tool
   ↓
HomeAssistantClient
   ↓
WebSocket
   ↓
Home Assistant
```

Damit kennt Needle nur abstrakte Funktionen wie:

```text
turn_on_light()
get_entity_state()
set_media_volume()
activate_scene()
```

Needle muss weder Home-Assistant-URLs noch Tokens noch WebSocket-Protokoll kennen.

---

# 3. Warum WebSocket?

Home Assistant stellt seine WebSocket-API unter

```text
/api/websocket
```

bereit.

Die Verbindung beginnt mit einer Authentifizierung und bleibt danach als Kommandoverbindung bestehen. Über dieselbe Verbindung können unter anderem Service-Aktionen aufgerufen und Events abonniert werden.

Für unseren Sprachagenten ist das praktisch:

```text
Needle
  ↓
Tool Call
  ↓
WebSocket
  ↓
HA
  ↓
Result/Event
```

Zusätzlich kann der Agent Zustandsänderungen abonnieren:

```text
Home Assistant
      │
      │ state_changed
      ▼
Python State Cache
```

Damit muss nicht für jede Benutzerfrage ein neuer HTTP-Request gemacht werden.

---

# 4. Authentifizierung

Für den ersten lokalen Prototyp:

**Home-Assistant Long-Lived Access Token**

Home Assistant unterstützt Long-Lived Access Tokens und akzeptiert diese sowohl für HTTP als auch für WebSocket-Authentifizierung.

Konfiguration:

```text
HA_URL=ws://192.168.x.x:8123/api/websocket
HA_TOKEN=...
```

Das Token wird **nicht** in den Python-Dateien gespeichert.

Stattdessen beispielsweise:

```text
.env
```

oder Umgebungsvariablen:

```bash
export HA_URL="ws://homeassistant:8123"
export HA_TOKEN="..."
```

Die `.env` kommt in `.gitignore`.

---

# 5. Home-Assistant-Modul

Neue Datei:

```text
homeassistant.py
```

Diese Klasse kapselt die komplette HA-Kommunikation.

```python
class HomeAssistantClient:

    async def connect(self):
        ...

    async def call_service(
        self,
        domain,
        service,
        target=None,
        data=None,
    ):
        ...

    async def get_state(self, entity_id):
        ...

    async def subscribe_state_changes(self):
        ...

    async def close(self):
        ...
```

Kein anderer Teil des Projekts soll direkt WebSocket-Nachrichten an HA schicken.

---

# 6. Persistente Verbindung

Beim Start:

```text
Python
  ↓
connect()
  ↓
Home Assistant WebSocket
  ↓
auth_required
  ↓
auth
  ↓
auth_ok
```

Home Assistant dokumentiert genau diesen Authentifizierungsablauf für `/api/websocket`.

Danach bleibt die Verbindung offen.

Bei einem Disconnect:

```text
connection lost
      ↓
reconnect
      ↓
re-authenticate
      ↓
restore subscriptions
```

Der Reconnect-Code sollte Teil des `HomeAssistantClient` sein.

---

# 7. State Cache

Neben Tool Calls soll der Agent möglichst schnell Informationen über HA-Geräte bekommen.

Deshalb:

```text
Home Assistant
      │
      ├── initial states
      │
      └── state_changed events
                │
                ▼
          Python State Cache
```

Beispiel:

```python
state_cache = {
    "light.wohnzimmer": {
        "state": "on",
        "brightness": 180,
    },

    "sensor.wohnzimmer_temperature": {
        "state": "21.4",
    }
}
```

Home Assistant besitzt eine zentrale State Machine für Entities, deren Zustände und Attribute über die API abgefragt werden können.

---

# 8. State Updates

Beim Start wird ein initialer Zustand geladen.

Danach wird auf:

```text
state_changed
```

abonniert.

Home Assistant unterstützt diese Event-Subscription offiziell über WebSocket.

Beispiel:

```text
HA:
light.wohnzimmer → on

        ↓

State Cache:
light.wohnzimmer.state = "on"
```

Wenn jemand anschließend manuell den Lichtschalter betätigt:

```text
HA:
light.wohnzimmer → off

        ↓
state_changed

        ↓
Python State Cache

        ↓
"off"
```

Damit ist der Cache nicht nur nach Tool Calls aktuell.

---

# 9. Entity Discovery

Needle muss wissen, welche Geräte überhaupt existieren.

Dafür soll die Anwendung beim Start die Home-Assistant-Konfiguration abrufen.

Mindestens:

```text
Entities
Areas
Devices
```

Home Assistant stellt dafür Registry-Daten über die WebSocket-API bereit. Die Entity-Registry enthält unter anderem Entity-ID, Namen, Area und Device-Zuordnung; die Area Registry beschreibt physische Bereiche und deren IDs/Namen.

Beispiel intern:

```python
entities = {
    "light.wohnzimmer": {
        "name": "Wohnzimmerlicht",
        "area": "wohnzimmer",
        "domain": "light",
    },

    "media_player.tv": {
        "name": "Fernseher",
        "area": "wohnzimmer",
        "domain": "media_player",
    },
}
```

---

# 10. Wichtig: Nicht alle HA-Entities an Needle senden

Es wäre keine gute Idee, z. B. 300 Home-Assistant-Entities ungefiltert in jeden Needle-Prompt zu werfen.

Stattdessen:

```text
Home Assistant
      ↓
Entity Registry
      ↓
Python
      ↓
relevante Tools / Context
      ↓
Needle
```

Die Tool-Schnittstelle bleibt klein.

Beispielsweise:

```text
light
switch
media_player
climate
scene
```

Weitere Domains können später hinzugefügt werden.

---

# 11. Needle-Tools

Die erste Version sollte mit wenigen stark typisierten Tools starten.

## Licht

```python
@needle.tool
def turn_on_light(entity_id: str):
    """Turn on a Home Assistant light."""
```

```python
@needle.tool
def turn_off_light(entity_id: str):
    """Turn off a Home Assistant light."""
```

## Schalter

```python
@needle.tool
def turn_on_switch(entity_id: str):
    """Turn on a Home Assistant switch."""
```

## Zustand

```python
@needle.tool
def get_entity_state(entity_id: str):
    """Get the current state of a Home Assistant entity."""
```

## Medien

```python
@needle.tool
def set_media_volume(entity_id: str, volume: float):
    """Set media player volume from 0.0 to 1.0."""
```

Damit kann Needle strukturierte Aufrufe erzeugen.

---

# 12. Tool → Home Assistant Mapping

Beispiel:

Needle:

```json
{
  "name": "turn_on_light",
  "arguments": {
    "entity_id": "light.wohnzimmer"
  }
}
```

Python:

```text
turn_on_light()
       ↓
HomeAssistantClient.call_service()
       ↓
domain = "light"
service = "turn_on"
target = {
    "entity_id": "light.wohnzimmer"
}
       ↓
WebSocket
       ↓
Home Assistant
```

Home Assistant unterstützt `call_service` über WebSocket mit `domain`, `service`, optionalem `target` und `service_data`.

---

# 13. Kein generisches "execute_any_service"

Nicht:

```python
call_any_home_assistant_service(
    domain,
    service,
    arbitrary_json
)
```

zumindest nicht in der ersten Version.

Besser:

```text
turn_on_light
turn_off_light
get_entity_state
set_volume
```

Warum?

Damit können die Parameter durch Needle klar strukturiert werden.

Zusätzlich kann jede Funktion intern validieren:

```text
Ist Entity vorhanden?
Ist Domain korrekt?
Ist Service erlaubt?
Sind Argumente gültig?
```

---

# 14. Entity-Auflösung

Ein Benutzer sagt:

```text
"mach das Licht im Wohnzimmer an"
```

Aber Needle kennt möglicherweise:

```text
light.deckenlampe_wohnzimmer
```

Deshalb sollte der Python-Layer zwischen natürlicher Sprache und tatsächlicher Entity-ID vermitteln.

Mögliche Struktur:

```text
Needle
  ↓
area = wohnzimmer
domain = light
  ↓
Entity Resolver
  ↓
light.deckenlampe_wohnzimmer
```

Dafür können die von HA gelieferten Entity-Namen und Areas genutzt werden. Die Registry stellt Area-Zuordnung und menschenlesbare Entity-Namen bereit.

---

# 15. Zwei mögliche Strategien

## Variante A: Needle bekommt echte Entity-IDs

```text
light.wohnzimmer_decke
light.wohnzimmer_lampe
```

Vorteil:

Sehr eindeutig.

Nachteil:

Natürliche Sprache muss diese IDs indirekt kennen.

## Variante B: Needle bekommt Namen

```text
Wohnzimmer Deckenlampe
Wohnzimmer Stehlampe
```

Python übersetzt:

```text
Wohnzimmer Stehlampe
        ↓
light.stehlampe_wohnzimmer
```

**Diese Struktur ist für den Voice-Agenten sinnvoller.**

---

# 16. Tool Context

Beim Start wird beispielsweise ein kleiner Kontext aufgebaut:

```json
{
  "lights": [
    {
      "id": "light.wohnzimmer_decke",
      "name": "Deckenlampe",
      "area": "Wohnzimmer"
    },
    {
      "id": "light.schlafzimmer",
      "name": "Schlafzimmerlicht",
      "area": "Schlafzimmer"
    }
  ]
}
```

Needle kann damit den richtigen Kandidaten auswählen.

Dieser Kontext sollte dynamisch aus Home Assistant erzeugt werden, statt hart im Code zu stehen.

---

# 17. Dry-Run-Modus

In der ersten HA-Version werden Aktionen noch nicht ausgeführt.

CLI:

```bash
python main.py --dry-run
```

Dann:

```text
[STT][final]
mach das licht im wohnzimmer an

[NEEDLE]
turn_on_light(
    entity_id="light.wohnzimmer_decke"
)

[HOME ASSISTANT][DRY RUN]
Would call:

domain: light
service: turn_on

target:
{
    "entity_id": "light.wohnzimmer_decke"
}
```

Erst:

```bash
python main.py
```

führt die Aktion wirklich aus.

---

# 18. Result Flow

Nach einem echten Tool Call soll die Pipeline später nicht sofort enden.

Statt:

```text
Needle
 ↓
Tool
 ↓
End
```

soll es werden:

```text
Needle
 ↓
Tool Call
 ↓
Home Assistant
 ↓
Tool Result
 ↓
Needle
 ↓
Final Response
```

Beispiel:

```text
User:
"Wie warm ist es im Wohnzimmer?"
```

Needle:

```text
get_entity_state(...)
```

HA:

```text
21.4 °C
```

Needle bekommt:

```text
Tool result:
21.4 °C
```

und erzeugt anschließend:

```text
Im Wohnzimmer sind es 21,4 °C.
```

---

# 19. Terminal-Debugging

Die Ausgabe soll erweitert werden:

```text
────────────────────────────────────────
HOME ASSISTANT
────────────────────────────────────────
Connection: OK
Host: 192.168.x.x:8123

────────────────────────────────────────
STT
────────────────────────────────────────
[final] mach das licht im wohnzimmer an

────────────────────────────────────────
NEEDLE
────────────────────────────────────────
Function:
turn_on_light

Arguments:
{
    "entity_id": "light.wohnzimmer_decke"
}

────────────────────────────────────────
HOME ASSISTANT
────────────────────────────────────────
Domain: light
Service: turn_on
Target:
{
    "entity_id": "light.wohnzimmer_decke"
}

Result:
success

────────────────────────────────────────
```

---

# 20. Fehlerbehandlung

Jede Ebene muss unabhängig Fehler melden.

## STT

```text
[STT ERROR]
```

## Needle

```text
[NEEDLE ERROR]
```

## Home Assistant

```text
[HA ERROR]
connection refused
```

oder:

```text
[HA ERROR]
authentication failed
```

oder:

```text
[HA ERROR]
entity not found
```

Der Prozess soll wegen eines einzelnen HA-Fehlers nicht abstürzen.

---

# 21. Reconnect

Wichtig für einen dauerhaft laufenden Voice-Agenten:

```text
WebSocket
    │
    ├── connected
    │
    ▼
state subscriptions
    │
    ▼
normal operation

connection lost
    │
    ▼
reconnect
    │
    ▼
authenticate
    │
    ▼
reload state
    │
    ▼
restore subscriptions
```

Der Agent soll nach einem Neustart von Home Assistant automatisch wieder funktionieren.

---

# 22. REST als Ergänzung

REST wird nicht komplett ausgeschlossen.

Home Assistant stellt unter `/api/` ebenfalls eine JSON-REST-API bereit und unterstützt authentifizierte Service Calls über:

```text
POST /api/services/<domain>/<service>
```

Für den Voice-Agenten bleibt aber:

```text
PRIMARY:
WebSocket

SECONDARY:
REST
```

REST eignet sich beispielsweise als einfacher Health Check:

```text
GET /api/
```

oder als Fallback für bestimmte Funktionen.

---

# 23. Konfiguration

Beispiel:

```text
config/
├── .env
└── config.toml
```

`.env`:

```text
HA_URL=ws://192.168.178.xxx:8123/api/websocket
HA_TOKEN=...
```

`config.toml`:

```toml
[homeassistant]
enabled = true
reconnect = true
dry_run = true

[homeassistant.tools]
lights = true
switches = true
media_players = true
climate = false
scenes = false
```

---

# 24. PC und Raspberry Pi

Das Ziel ist:

```text
                Gemeinsamer Code
                       │
        ┌──────────────┴──────────────┐
        │                             │
       PC                         Raspberry Pi
        │                             │
   x86_64 Python                  ARM64 Python
        │                             │
   Zipformer                    Zipformer
   Needle 3                     Needle 3
        │                             │
        └──────────────┬──────────────┘
                       │
               HomeAssistantClient
                       │
                       ▼
                  Home Assistant
```

Der Home-Assistant-Teil merkt nicht, ob der Client auf:

```text
PC
```

oder:

```text
Raspberry Pi 5
```

läuft.

---

# 25. Gemeinsames Interface

Die obere Anwendung soll nur diese Schnittstelle sehen:

```python
class HomeAssistantClient:
    async def call_service(...):
        ...

    async def get_state(...):
        ...

    async def get_entities(...):
        ...
```

Darunter kann sich später ändern:

```text
PC:
Python WebSocket

Pi:
Python WebSocket
```

Es gibt keinen Grund, dafür eine zweite HA-Integration zu entwickeln.

---

# 26. Projektstruktur nach Integration

```text
voice-agent/
│
├── main.py
├── config.py
│
├── audio/
│   ├── __init__.py
│   ├── base.py
│   └── microphone.py
│
├── stt/
│   ├── __init__.py
│   └── zipformer.py
│
├── llm/
│   ├── __init__.py
│   └── needle.py
│
├── homeassistant/
│   ├── __init__.py
│   ├── client.py
│   ├── entities.py
│   ├── state.py
│   └── tools.py
│
├── tools/
│   ├── __init__.py
│   └── registry.py
│
├── models/
│   ├── zipformer/
│   └── needle/
│
├── .env
├── .gitignore
├── requirements.txt
└── README.md
```

---

# 27. Entwicklungsreihenfolge

## HA1

Home Assistant Client alleine testen:

```text
Python
 ↓
WebSocket
 ↓
Home Assistant
```

Test:

```text
connect
get state
call service
disconnect
```

---

## HA2

Entity Discovery:

```text
Home Assistant
 ↓
entities
 ↓
Python registry
 ↓
Terminal
```

---

## HA3

State Cache:

```text
HA
 ↓
state_changed
 ↓
local cache
```

---

## HA4

Needle Dummy Tool:

```text
Text
 ↓
Needle
 ↓
turn_on_light()
 ↓
Terminal
```

Noch ohne HA.

---

## HA5

Tool an HA anschließen:

```text
Needle
 ↓
turn_on_light()
 ↓
HA Client
 ↓
Home Assistant
```

---

## HA6

Tool Results:

```text
Needle
 ↓
Tool
 ↓
HA
 ↓
Result
 ↓
Needle
```

---

## HA7

Gesamte Pipeline:

```text
Microphone
 ↓
Zipformer
 ↓
Endpoint
 ↓
Needle 3
 ↓
HA Tool
 ↓
Home Assistant
 ↓
Result
 ↓
Needle 3
 ↓
Terminal
```

---

# 28. Später: echte Voice-Assistant-Ausgabe

Sobald das Terminal stabil funktioniert:

```text
Needle Final Response
        ↓
TTS
        ↓
Audio Output
```

Dann wird aus:

```text
"Mach das Licht an."
```

nicht nur:

```text
[TOOL CALL]
```

sondern beispielsweise:

```text
[ASSISTANT]
Das Licht ist jetzt an.
```

mit Sprachausgabe.

---

# 29. Zielarchitektur

```text
                         VOICE AGENT
┌───────────────────────────────────────────────────────────┐
│                                                           │
│  Audio Source                                             │
│       │                                                   │
│       ▼                                                   │
│  Streaming Zipformer                                      │
│       │                                                   │
│       ▼                                                   │
│  Final Transcript                                         │
│       │                                                   │
│       ▼                                                   │
│  Needle 3                                                 │
│       │                                                   │
│       ▼                                                   │
│  Tool Dispatcher                                          │
│       │                                                   │
│       ▼                                                   │
│  HomeAssistantClient                                      │
│       │                                                   │
└───────┼───────────────────────────────────────────────────┘
        │
        │ WebSocket
        ▼
┌───────────────────────────────────────────────────────────┐
│                    HOME ASSISTANT                         │
│                                                           │
│   Entities       Areas       Devices       Services       │
│       │            │            │             │           │
│       └────────────┴────────────┴─────────────┘           │
│                         │                                 │
│                    State Events                            │
└───────────────────────────────────────────────────────────┘
```

## Designziel

Die Verantwortlichkeiten bleiben strikt getrennt:

```text
Zipformer  = Was wurde gesagt?
Needle 3   = Was soll daraus als Tool Call werden?
HA Client  = Wie kommunizieren wir mit Home Assistant?
Tools      = Welche HA-Aktionen sind erlaubt?
State      = Was ist aktuell in Home Assistant?
```

Dadurch kann zunächst alles auf dem PC entwickelt und gemessen werden. Der spätere Raspberry-Pi-Port betrifft dann primär **Audio-Eingang, Modell-Builds und Performance**, nicht die eigentliche Home-Assistant-Logik.

