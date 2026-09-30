# Needle 3 / Laya Conversation

**Conversation-Agent** für Home Assistant über das **Wyoming-Protokoll**
(`handle`-Domain) – nativ auswählbar unter **Einstellungen →
Sprachassistenten → Assistant → Conversation**.

Zwei umschaltbare **Entscheidungsschichten**:

| Backend | Was es tut |
|---|---|
| **`needle`** | Needle 3 – Tool-Calling (wählt Tool + Argumente) |
| **`laya`** | Laya – System-1-Entscheidungsmodell, wählt kontextbewusst das Gerät |

Davor liegt immer eine **deterministische Ebene**:

```text
Text
 ├── 0) STT-Autokorrektur   (ähnlich klingende Wörter, Fuzzy + Phonetik)
 ├── 1) Fast-Path           (deutsche Kommandos ohne Modell)
 ├── 2) Needle 3 / Laya     (umschaltbar)
 └── 3) HA-Fallback         (Home Assists eigener Agent)
```

## Fast-Path (deterministisch)

Für die häufigen Sätze wird die Aktion über Schlüsselwörter und das Gerät über
den Resolver bestimmt – ohne Modell, reproduzierbar. Erkannt werden:
**an/aus**, **dimmen**, **Zustand**, **Lautstärke** (absolut und relativ),
**Temperatur**.

## STT-Autokorrektur

Spracherkennung verwechselt ähnlich klingende Wörter. Die Korrektur bildet
solche Tokens auf das Domänen-Vokabular ab (Entity-Namen, Aliase, Areas/Floors,
Kommandowörter):

```text
"schalte schreibtischlame ein"   → "Schreibtischlampe"
"mach die kaffemaschine an"      → "Kaffeemaschine"
"mach das licht im wohnzimer an" → "Wohnzimmer"
"schalte den fernsehr ein"       → "fernseher"
```

- **Kölner Phonetik** + **RapidFuzz**, nur eindeutige Treffer (Score + Vorsprung)
- Kommandoverben („schalte", „mach", …) und bereits bekannte Wörter werden
  **nie** verändert
- **Validierung**: korrigiert wird nur, wenn das Ergebnis ein Kommando
  auflösbar macht
- Korrekturen erscheinen im **Verlauf + Log**
- Schwellen im UI einstellbar

## Namensauflösung (Fuzzy + Phonetik + Kontext)

| Methode | Wofür |
|---|---|
| Normalisierung | Kleinschreibung, Umlaute, Sonderzeichen |
| Kölner Phonetik | deutsche STT-Varianten |
| RapidFuzz | `ratio`, `partial_ratio`, `token_set_ratio` |
| Aliase | aus der HA-Entity-Registry |
| Area/Floor | „das licht im wohnzimmer" |
| Typ-Stichwörter | licht→light, fernseher→media_player, steckdose→switch |
| Confidence + Margin | nur eindeutige Treffer |

## Laya-Backend

[Laya](https://huggingface.co/convaiinnovations/laya) (Convai Innovations,
Apache-2.0) ist ein **nicht-autoregressives System-1-Entscheidungsmodell**
(ModernBERT/mmBERT, ~322–421M): Es beantwortet typisierte Fragen (`choice`,
`score`, `noul`) mit kalibrierten Wahrscheinlichkeiten in einem Forward-Pass –
**ohne** Textgenerierung.

**Wichtig:** Laya klassifiziert deutsche **Ein/Aus-Polarität unzuverlässig**
(„kaffeemaschine **aus**" → `turn_on`). Deshalb macht der Laya-Backend:

1. **Aktion** aus den zuverlässigen Regel-Schlüsselwörtern (Laya nur, wenn die
   Regeln nichts finden).
2. **Gerät** wählt Laya **kontextbewusst** aus den Kandidaten des Resolvers –
   das ist seine Stärke:

```text
"mach das wohnzimmer an"  → Kandidaten: Deckenlampe, Fernseher
                          → Laya wählt die Deckenlampe
```

Einstellungen: Modell (`multilingual`/`english`), Confidence-Schwelle,
max. Kandidaten, Preload. Ist Laya unsicher (unter Schwelle) → HA-Fallback.
Der Test-Button **„Laya testen"** zeigt Aktion + Confidence + Latenz.

> `laya` braucht **torch** → das Add-on-Image ist größer und der erste Start
> lädt das Modell nach `/data/.cache/huggingface`. Deshalb unterstützt dieses
> Add-on nur **aarch64 + amd64** (kein armv7; torch hat keine 32-bit-ARM-Wheels).

## Sicherheit

- **Grounding-Prüfung**: Call auf ein Gerät, das im Satz nicht vorkommt → verworfen.
- **Polaritäts-Prüfung**: „an/aus" passt nicht zur Funktion → verworfen.
- **HA-Fallback**: liefert das Backend nichts Brauchbares, übernimmt HA.

## Einrichtung

1. Add-on installieren und starten (erster Start lädt Needle-Engine ~35 MB und –
   bei Bedarf – das Laya-Modell).
2. HA entdeckt den Wyoming-Dienst automatisch (Supervisor-Discovery).
3. **Sprachassistenten → Assistant → Conversation** auf den Wyoming-Eintrag.
4. Reihenfolge: STT (`sherpa_stt`) → Conversation (dieses Add-on) → TTS (Piper).

## Konfiguration (Seitenleiste)

| Einstellung | Bedeutung |
|---|---|
| **Entscheidungsschicht** | `needle` oder `laya` |
| **Laya-Modell / Schwelle / Kandidaten / Preload** | nur `laya` |
| **Dry-Run** | Aktionen nur anzeigen (Default: an) |
| **Fast-Path** | eindeutige Kommandos ohne Modell |
| **HA-Agent als Fallback** | Sicherheitsnetz |
| **Grounding-/Polaritätsprüfung** | siehe oben |
| **STT-Autokorrektur** + Schwellen | siehe oben |
| **Fehlerdetails in der Antwort** | hängt die Fehlerursache an |
| **Domains / Tools** | was erlaubt ist / was Needle sieht (≤ 5) |
| **Schwellen** | Resolver (Score/Vorsprung/Floor/Tool), Low-Confidence, Max-Tokens |
| **Log-Aufzeichnung** | stdout/stderr im Ringpuffer (abschaltbar) |
| **HA-URL / HA-Token** | nur nötig außerhalb von HAOS |
| **Debug-Logging** | ausführliche Logs |

## Debugging

- **Warnbanner** oben: Needle-Init-Fehler, fehlende HA-Entities, ausgeschalteter
  Fast-Path – inklusive genauer Meldung.
- **Diagnose**: HA, `cactus-needle`, Engine-Cache, **Backend**, `laya`, `torch`,
  Modell-Cache, Resolver, Fast-Path, Tools, Entities.
- **Resolver-Test**: Area/Floor, Kandidaten mit Scores, erkanntes Kommando.
- **Backend testen** / **Laya testen**: mit Traceback bzw. Confidence.
- **Logs**: letzte Zeilen inkl. `print()` und Tracebacks; „Logs leeren".
- Endpunkte: `GET /api/diagnostics`, `GET /api/resolve?text=…`,
  `POST /api/backend/test`, `POST /api/laya/test`, `GET /api/logs`,
  `POST /api/logs/clear`.

**Automatik:** Entities werden beim Start mit Retry im Hintergrund geladen und –
solange leer – alle 15 s erneut versucht. Needle-Cache und HF-Modelle liegen
über `HOME=/data` persistent in `/data/.cache/...` und überstehen Updates.

## Grenzen

Needle erzeugt keinen freien Text; Antworten werden aus den Tool-Ergebnissen
gebaut (Templates im UI anpassbar). Laya klassifiziert deutsche Aktions-Polarität
unzuverlässig – daher steuert es nur die Geräteauswahl. Für echte freie
Unterhaltung eignet sich ein LLM-Conversation-Agent in Home Assistant; dieses
Add-on ist auf **zuverlässige Gerätesteuerung per Sprache** optimiert.

## Docker (ohne HAOS)

```bash
docker build -t needle-agent ./addons/needle_agent
docker run -d --name needle-agent -p 10300:10300 -p 8000:8000 \
  -v needle-data:/data \
  -e HA_URL=http://homeassistant.local:8123 \
  -e HA_TOKEN=<long-lived-token> \
  needle-agent
```
