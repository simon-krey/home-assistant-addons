# Needle 3 Conversation

**Conversation-Agent** für Home Assistant über das **Wyoming-Protokoll**
(`handle`-Domain) – nativ auswählbar unter **Einstellungen →
Sprachassistenten → Assistant → Conversation**.

Es gibt **nur Needle 3** als Modell (keine LLMs). Damit die Gerätesteuerung
trotzdem sehr zuverlässig ist, gibt es zwei Ebenen:

```text
Text
 ├── 1) Fast-Path   (deterministisch, kein Modell)  ← eindeutige Kommandos
 ├── 2) Needle 3    (Tool-Calling)                  ← alles andere
 └── 3) HA-Fallback (Home Assists Agent)            ← wenn nichts passt
```

## 1) Fast-Path – deutsche Kommandos ohne Modell

Für die häufigen Sätze wird die Aktion über Schlüsselwörter und das Gerät über
den Resolver bestimmt. Ist beides eindeutig, wird **direkt ausgeführt** –
schnell, reproduzierbar und ohne Modellfehler.

Erkannt werden: **an/aus**, **dimmen** (Prozent), **Zustand**, **Lautstärke**
(absolut und relativ „lauter/leiser"), **Temperatur**.

## 2) Namensauflösung (Fuzzy + Phonetik + Kontext)

Die Methoden, die sich in der Praxis bewährt haben (vgl. HA-Assist und
`ha-intent-resolver-agent`):

| Methode | Wofür |
|---|---|
| **Normalisierung** | Kleinschreibung, Umlaute (ä→ae), Sonderzeichen |
| **Kölner Phonetik** | deutsche STT-Varianten: „kaffemaschine" → Kaffeemaschine, Meyer/Maier |
| **RapidFuzz** | `partial_ratio`, `token_set_ratio`, `WRatio` |
| **Aliase** | aus der HA-Entity-Registry (wie in Assist) |
| **Area/Floor** | „das licht im wohnzimmer" → Area + Domain |
| **Typ-Stichwörter** | licht/lampe → light, fernseher/tv → media_player, steckdose → switch |
| **Confidence + Margin** | nur eindeutige Treffer werden akzeptiert |

Beispiele:

```text
"mach das licht im wohnzimmer an"   → Area Wohnzimmer + Domain light → Wohnzimmer Deckenlampe
"mach den schreibtisch an"          → Alias "Schreibtisch"           → Schreibtischlampe
"mach die kaffemaschine aus"        → phonetisch (Tippfehler)        → Kaffeemaschine
"dimme das kuechenlicht auf 30 %"   → Licht + Zahl                  → set_brightness 30
```

Ist ein Kommando **nicht eindeutig**, geht es an Needle – und der Text bekommt
vorher einen Gerätehinweis (`… [Gerät: Schreibtischlampe]`), damit Needle den
kanonischen Namen sieht.

## 3) Sicherheit

- **Grounding-Prüfung:** Ein Call auf ein Gerät, das im Satz nicht vorkommt,
  wird verworfen (verhindert Fehlschaltungen).
- **Polaritäts-Prüfung:** passt „an/aus" nicht zur gewählten Funktion → verworfen.
- **HA-Fallback:** liefert Needle nichts Brauchbares, übernimmt Home Assists
  eigener Agent.

## Einrichtung

1. Add-on installieren und starten (erster Needle-Start lädt Engine + Weights
   ~35 MB von Hugging Face; danach offline).
2. HA entdeckt den Wyoming-Dienst automatisch (Supervisor-Discovery).
3. **Sprachassistenten → Assistant → Conversation** auf den Wyoming-Eintrag.
4. Reihenfolge: STT (`sherpa_stt`) → Conversation (dieses Add-on) → TTS (Piper).

## Konfiguration (Seitenleiste)

| Einstellung | Bedeutung |
|---|---|
| **Dry-Run** | Aktionen nur anzeigen (Default: an) |
| **Fast-Path** | eindeutige Kommandos ohne Modell ausführen |
| **HA-Agent als Fallback** | Sicherheitsnetz |
| **Grounding-/Polaritätsprüfung** | siehe oben |
| **Fehlerdetails in der Antwort** | hängt die Fehlerursache an |
| **Domains** | welche Domains überhaupt erlaubt sind |
| **Tools** | Tool-Auswahl für Needle (Empfehlung ≤ 5) |
| **Max. Tool-Schritte** | mehrstufige Calls |
| **Sprache / System-Fakten** | für Needle/HA |
| **Entity-Refresh** | Sekunden zwischen Registry-Aktualisierungen |
| **Verlauf behalten** | Anzahl Einträge |
| **Antwort-Templates** | JSON, überschreibt die Standard-Antworten |
| **HA-URL / HA-Token** | nur nötig außerhalb von HAOS |
| **Debug-Logging** | ausführliche Logs |
| **Log-Aufzeichnung** | stdout/stderr im Ringpuffer (abschaltbar); „Logs leeren" im Diagnose-Bereich |

### Schwellen (Namensauflösung)

Alle Schwellen sind im UI einstellbar (Default in Klammern):

| Einstellung | Bedeutung |
|---|---|
| **Min. Score** (0.72) | Mindest-Score, damit ein Gerät als Treffer gilt |
| **Min. Vorsprung** (0.12) | Mindest-Abstand zum Zweitplatzierten (sonst „uneindeutig") |
| **Kandidaten-Floor** (0.55) | darunter wird ein Kandidat gar nicht erst betrachtet |
| **Tool-Treffer** (0.60) | Schwelle für Gerätenamen, die Needle liefert (Tool-Argument/Grounding) |
| **Low-Confidence** (0.10) | darunter gilt Needles Antwort als unsicher |
| **Needle Max-Tokens** (256) | maximale Länge einer Needle-Antwort |

Höhere Werte = strenger (mehr Fälle gehen an Needle bzw. den HA-Fallback),
niedrigere Werte = großzügiger.

Beim **Speichern** werden nur tatsächlich geänderte Felder übertragen – so
können Schalter (Fast-Path, Fallback, …) nicht versehentlich zurückgesetzt
werden.

> Der **Fast-Path** nutzt die aktivierten *Domains*, nicht die Needle-Tool-Liste.
> So funktioniert z. B. „Kaffeemaschine aus" auch, wenn `turn_off_switch` nicht
> unter den 5 Needle-Tools ist.

## Debugging

**Warnhinweis oben in der UI:** Wenn Needle nicht initialisiert werden konnte
oder der Fast-Path ausgeschaltet ist, erscheint ein roter Hinweis – inklusive
der genauen Fehlermeldung. Typisches Symptom dafür ist die Antwort
„Entschuldigung, das habe ich nicht verstanden." vom HA-Agenten, obwohl der
Resolver das Gerät findet.

- **Diagnose** – prüft HA, `cactus-needle`, Engine-Cache, **Needle-Backend**,
  Resolver (Entities/Aliase/Areas), Fast-Path, Tools.
- **Resolver-Test** – zeigt für einen Satz Area/Floor, Kandidaten mit Scores
  und ob ein Fast-Path-Kommando erkannt wurde.
- **Backend testen** – Minimal-Prompt durch Needle, mit Traceback.
- **Logs** – letzte Logzeilen inkl. `print()`-Ausgaben und Tracebacks
  (über **Log-Aufzeichnung** abschaltbar, über **Logs leeren** löschbar).
- Endpunkte: `GET /api/diagnostics`, `GET /api/resolve?text=…`,
  `POST /api/backend/test`, `GET /api/logs`, `POST /api/logs/clear`.

## Endpunkte

| Endpoint | Zweck |
|---|---|
| `GET /api/status` | HA, Tools, Entities (inkl. Aliase/Area/Floor), Stats |
| `GET/POST /api/settings` | Einstellungen |
| `POST /api/settings/reset` | auf Add-on-Optionen |
| `POST /api/refresh` | Entities + Registry neu laden |
| `POST /api/test` | Text direkt verarbeiten |
| `GET /api/resolve` | Namensauflösung testen |
| `GET /api/diagnostics` / `GET /api/logs` / `POST /api/logs/clear` / `POST /api/backend/test` | Debugging |
| `GET/DELETE /api/history` | Verlauf |
| `GET /health` | Health-Check |

## Grenzen

Needle 3 ist ein **Tool-Calling-Modell**, kein Chat-Modell: es erzeugt keine
freien Antworten. Antworten werden aus den Tool-Ergebnissen gebaut
(Templates im UI anpassbar). Für echte freie Unterhaltung eignet sich ein
LLM-Conversation-Agent in Home Assistant; dieses Add-on ist auf
**zuverlässige Gerätesteuerung per Sprache** optimiert.

## Docker (ohne HAOS)

```bash
docker build -t needle-agent ./addons/needle_agent
docker run -d --name needle-agent -p 10300:10300 -p 8000:8000 \
  -v needle-data:/data \
  -e HA_URL=http://homeassistant.local:8123 \
  -e HA_TOKEN=<long-lived-token> \
  needle-agent
```
