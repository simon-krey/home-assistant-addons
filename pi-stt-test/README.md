# Pi STT Realtime Test

Kleiner, eigenständiger Test: **Läuft sherpa-onnx Streaming-STT auf dem
Raspberry Pi in Echtzeit?**

Die App bringt eine Web-UI mit, die drei Dinge messen kann:

1. **Realtime-Mikrofon-Test** – Browser-Mikrofon → WebSocket → Pi-STT, live
   Transkript + laufender RTF.
2. **Datei-Benchmark** – WAV hochladen, RTF messen (`Dekodierzeit / Audiodauer`).
3. **Echtzeit-Simulation** – WAV im Aufnahme-Tempo einspeisen und prüfen, ob die
   Dekodierung hinterherkommt (max. Lag).

> **RTF < 1** → schneller als Echtzeit (realtime-fähig).
> **RTF > 1** → kommt nicht hinterher.

---

## Wichtig zu Home Assistant OS

Auf **HAOS läuft Docker intern**, wird aber vom Supervisor verwaltet. Man kann
dort **nicht** einfach `docker run` machen. Der vorgesehene Weg für eigene
Container ist ein **lokales Add-on** – das ist technisch genau ein Docker-
Container, den der Supervisor baut und startet.

Deshalb gibt es hier zwei Betriebsarten mit **demselben Code**:

| Weg | Plattform | Start |
|---|---|---|
| **A: HAOS-Add-on** | Home Assistant OS | Supervisor baut `stt_test/Dockerfile` |
| **B: Docker** | Raspberry Pi OS / anderes Linux | `docker compose up` |

---

## A) Als Home Assistant Add-on (HAOS)

1. Den Ordner `pi-stt-test/` auf den Pi bringen (z. B. per Samba/SSH/SCP oder
   als Git-Repo).
2. In Home Assistant: **Einstellungen → Add-ons → Add-on-Store → ⋮ →
   Repositories** und die URL/den Pfad zum Repository hinzufügen
   (der Ordner mit `repository.yaml`).
3. **STT Realtime Test** installieren und starten. Der erste Start lädt das
   Modell (57 MB) nach `/data/models` – das dauert ein paar Minuten.
4. Über die Seitenleiste **STT Test** öffnen (HA-Ingress).

Optionen im Add-on: `model` (`de` oder `small-en`) und optional `model_url`.

### Mikrofon im Browser (secure context)

`getUserMedia` funktioniert nur in einem **secure context**:

- ✅ HA über **HTTPS** (Nabu Casa, Reverse Proxy, `https://…`)
- ✅ `http://localhost`
- ❌ `http://homeassistant.local:8123` (reines HTTP im LAN)

Ist die Seite nicht sicher, zeigt die UI einen Hinweis. Dann einfach den
**Datei-Benchmark** oder die **Echtzeit-Simulation** nutzen – die brauchen kein
Mikrofon und messen die Pi-Performance genauso gut.

Workaround für Chrome/Edge auf dem eigenen Rechner: die Pi-URL unter
`chrome://flags/#unsafely-treat-insecure-origin-as-secure` eintragen und den
Flag aktivieren (nur zum Testen, nicht dauerhaft).

---

## B) Mit Docker (Raspberry Pi OS)

```bash
cd pi-stt-test
docker compose up -d --build
# UI: http://<pi-ip>:8000
```

Oder ohne Compose:

```bash
docker build -t stt-test ./stt_test
docker run -d --name stt-test -p 8000:8000 -v stt-models:/data stt-test
```

Logs: `docker logs -f stt-test`

> Hinweis: Über `http://<pi-ip>:8000` ist der Browser-Mikrofon-Test wegen des
> secure-context-Themas gesperrt. Der Datei-Benchmark funktioniert trotzdem.
> Für den Mikrofon-Test z. B. einen HTTPS-Reverse-Proxy davorsetzen.

---

## Direkt auf dem Pi (ohne Docker)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r stt_test/requirements.txt
cd stt_test
MODELS_DIR=./models python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

---

## Konfiguration (Umgebungsvariablen)

| Variable | Default | Bedeutung |
|---|---|---|
| `MODELS_DIR` | `/data/models` (Add-on) bzw. `./models` | Modellablage |
| `STT_MODEL` | `de` | `de` (Kroko, 70 MB) oder `small-en` (20M, schnell) |
| `STT_MODEL_URL` | – | eigenes Modell-Archiv (.tar.bz2) |
| `STT_NUM_THREADS` | `2` | sherpa-onnx Threads (auf dem Pi 1–4 testen) |
| `STT_PROVIDER` | `cpu` | Ausführungs-Provider |
| `STT_MODEL_TYPE` | modellabhängig | `zipformer2` für Kroko, sonst leer |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind-Adresse |

### Modellwahl

| Modell | Größe | Sprache | Eignung |
|---|---|---|---|
| `de` | ~70 MB Encoder (fp32) | Deutsch | genauere Ergebnisse, mehr CPU |
| `small-en` | ~20M Parameter | Englisch | deutlich schneller, gut zum RTF-Test |

Für schwache Pis (Pi 3, Zero 2 W) ist `small-en` der schnellere Test.
Falls ein int8-Modell vorliegt, wird es automatisch bevorzugt.

---

## API

| Endpoint | Zweck |
|---|---|
| `GET /` | Web-UI |
| `GET /health` | Health-Check |
| `GET /api/info` | Modell- und Systeminfo |
| `GET /api/metrics` | CPU, RAM, Load, Temperatur |
| `POST /api/model` | Modell wechseln (`{"model":"small-en"}`) |
| `GET /api/selftest` | Benchmark mit der Modell-Test-WAV |
| `POST /api/benchmark?realtime=true\|false` | WAV-Benchmark (multipart) |
| `WS /ws/stt` | Streaming: binäre int16-PCM @16 kHz rein, JSON-Events raus |

---

## Grobe Erwartung (bitte messen!)

Die Zahlen sind Erfahrungswerte und hängen stark von Modell, Threads und
Kühlung ab:

| Gerät | `small-en` | `de` (Kroko fp32) |
|---|---|---|
| Pi 5 (4× A76) | RTF ≪ 0.2 | ~0.2–0.4 |
| Pi 4 (4× A72) | ~0.2–0.4 | ~0.5–0.9 |
| Pi 3 (4× A53) | ~0.5–1.0 | > 1 (nicht realtime) |
| Zero 2 W | ~0.8–1.5 | > 1.5 |

Faustregel: RTF sollte **deutlich unter 1** liegen, damit neben der STT noch
Reserve für Needle/Tool-Calls bleibt.
