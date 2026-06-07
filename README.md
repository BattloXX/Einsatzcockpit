# Einsatzleiter-Hilfswerkzeug

**Digitales Einsatzleiter-Werkzeug für Feuerwehren** — Multi-User, Multi-Organisations-fähig, Echtzeit.


[![CI](https://github.com/BattloXX/Einsatzleiter-Hilfswerkzeug/actions/workflows/ci.yml/badge.svg)](https://github.com/BattloXX/Einsatzleiter-Hilfswerkzeug/actions)
![Python](https://img.shields.io/badge/python-3.14-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green)
![Version](https://img.shields.io/badge/version-2.0.0-orange)

---

## Überblick

Das Werkzeug ersetzt ein Single-File-HTML-Tool durch eine vollwertige Webapp, die Einsatzleitern und Schriftführern eine strukturierte, Echtzeit-fähige Arbeitsumgebung bietet.

**Zielgruppe:** Einsatzleiter, Schriftführer und Atemschutz-Überwacher österreichischer Feuerwehren.

**Kern-Prinzipien:**
- Mehrere Geräte (Tablet, PC, Mobilgerät) arbeiten gleichzeitig am selben Einsatz
- Vollständiges Audit-Log — jede Änderung wird protokolliert (Zeitreise-Funktion)
- Multi-Tenancy — mehrere Organisationen auf einer Instanz, klar getrennt
- Offline-fähige PWA — eingeschränkte Nutzung auch ohne Netzverbindung

---

## Features

| Feature | Beschreibung |
|---------|-------------|
| **Echtzeit-Kanban-Board** | WebSocket-basiertes Board für Einsatzkräfte, Aufgaben, Fahrzeuge und Meldungen |
| **Atemschutzüberwachung** | Rückzugsdruckberechnung, Zeitmessung, gesetzlich verpflichtend |
| **Media-Galerie** | Bilder, PDFs und Videos direkt an Aufträge anhängen; sichere Auslieferung über Auth-Route |
| **Multi-Org-Support** | Mehrere Feuerwehren, gemeinsame Einsätze, eigene Stammdaten je Org |
| **REST-API** | Automatische Einsatzanlage aus dem Alarmierungssystem (idempotent) |
| **PDF-Export** | Einsatzbericht mit Zeitstempeln, Audit-Log, Geretteten als WeasyPrint-PDF |
| **Archiv & Zeitreise** | Vollständiges Änderungsprotokoll; jeden Zustand in der Vergangenheit anzeigen |
| **Web-Push-Benachrichtigungen** | PWA Push bei neuem Einsatz (VAPID) |
| **QR-Code-Login** | Schnell-Login für Tablet-Stationen ohne Passwort-Eingabe |
| **In-App ZIP-Update** | Neue Version per Upload einspielen, kein SSH erforderlich |
| **Statistik-Dashboard** | Einsatzauswertung nach Typ, Zeit, Fahrzeug |
| **Stammdaten-Verwaltung** | Fahrzeuge, Mitglieder, Qualifikationen (AGT-Ablaufdaten), Alarmtypen |
| **Großschadenslage** | Phasen-Kanban für Massenanfall-Ereignisse: Einsatzstellen, Abschnitte, Stabsfunktionen, Funkliste, Bürgermeldungen, Pressemeldung |
| **KI-Assistent (✨)** | Auftragsvorschläge, Lage-Ticker-Hinweise, Lagebild und automatische Priorisierung neuer Einsatzstellen via Anthropic Claude; opt-in pro Instanz |

---

## Tech-Stack

| Schicht | Technologie |
|---------|-------------|
| Backend | **FastAPI** (Python 3.14) + Uvicorn/Gunicorn |
| ORM / Migrationen | **SQLAlchemy 2.x** + **Alembic** |
| Datenbank | **MariaDB 10.11+** (utf8mb4, InnoDB) |
| Templates | **Jinja2** (Server-Rendering, keine Build-Zeit) |
| Frontend-Reaktivität | **HTMX** + **Alpine.js** |
| Drag & Drop | **SortableJS** |
| CSS-Framework | **Tailwind CSS 3** (lokaler Build, kein CDN) |
| Realtime | FastAPI **WebSockets** (Pub/Sub je Einsatz) |
| Auth | Session-Cookies + **bcrypt** + **itsdangerous** |
| PDF-Erzeugung | **WeasyPrint** |
| Web-Push | **pywebpush** (VAPID) |
| Bild-Verarbeitung | **Pillow** + **pillow-heif** (HEIC/iPhone) |
| Video-Transcode | **ffmpeg** (subprocess, H.264/AAC, 720p) |
| PDF-Metadaten | **pypdf** (Seitenanzahl) |
| Rate-Limiting | **slowapi** |
| QR-Code | **qrcode[pil]** |
| PWA | Service Worker + Web App Manifest |
| Deployment | Gunicorn + UvicornWorker, Port **8092**, NGINX, systemd |

---

## Setup (Lokale Entwicklung)

### Voraussetzungen

- Python 3.14
- Node.js 20+ (für Tailwind CSS Build)
- MariaDB 10.11+ (oder Docker)
- Optional: `ffmpeg` für Video-Uploads

### Schritte

```bash
# 1. Repository klonen
git clone https://github.com/BattloXX/Einsatzleiter-Hilfswerkzeug.git
cd Einsatzleiter-Hilfswerkzeug

# 2. Python venv anlegen und aktivieren
python3.14 -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .venv\Scripts\activate           # Windows

# 3. Python-Abhängigkeiten installieren
pip install -e ".[dev]"

# 4. Frontend-Abhängigkeiten installieren und CSS bauen
npm install
npm run build          # einmalig, erzeugt app/static/css/app.css

# 5. MariaDB starten (Docker-Variante)
docker run -d --name einsatzleiter-db \
  -e MARIADB_ROOT_PASSWORD=root \
  -e MARIADB_DATABASE=einsatzleiter \
  -e MARIADB_USER=einsatzleiter \
  -e MARIADB_PASSWORD=devpassword \
  -p 3306:3306 mariadb:10.11

# 6. Konfigurationsdatei anlegen
cp .env.example .env
# In .env anpassen: DATABASE_URL und SECRET_KEY (mindestens 32 Zeichen)

# 7. Datenbankschema migrieren + Seed-Daten einspielen
alembic upgrade head
python -m app.seed_data

# 8. Entwicklungsserver starten
uvicorn app.main:app --reload --port 8092
# → http://localhost:8092  (Login: admin / admin)
```

Während der Entwicklung CSS-Änderungen automatisch kompilieren:
```bash
npm run dev    # Tailwind im Watch-Modus
```

---

## Setup (Produktion — Debian 12 + CloudPanel)

Vollständige Anleitung: [`deploy/README-Deployment.md`](deploy/README-Deployment.md)

### Systempakete

```bash
sudo apt-get update
sudo apt-get install -y \
    python3.14 python3.14-venv python3.14-dev \
    libmariadb-dev libpango-1.0-0 libpangoft2-1.0-0 \
    build-essential ffmpeg
```

`ffmpeg` ist für Video-Uploads erforderlich. Ohne ffmpeg werden nur Bilder und PDFs akzeptiert.

### App installieren

```bash
git clone https://github.com/BattloXX/Einsatzleiter-Hilfswerkzeug.git \
    /home/clp-einsatz/htdocs/einsatzleiter
cd /home/clp-einsatz/htdocs/einsatzleiter

python3.14 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
nano .env   # DATABASE_URL, SECRET_KEY und ggf. SMTP konfigurieren

alembic upgrade head
python -m app.seed_data
```

Das CSS ist fertig gebaut im Repository enthalten (`app/static/css/app.css`). Node.js wird auf dem Server **nicht** benötigt.

### systemd-Service

```bash
sudo cp deploy/einsatzleiter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now einsatzleiter
# → App läuft auf Port 8092
```

Inhalt `deploy/einsatzleiter.service`:
```ini
[Unit]
Description=Einsatzleiter-Hilfswerkzeug
After=network.target

[Service]
User=clp-einsatz
WorkingDirectory=/home/clp-einsatz/htdocs/einsatzleiter
ExecStart=/home/clp-einsatz/htdocs/einsatzleiter/.venv/bin/gunicorn \
    app.main:app \
    -k uvicorn.workers.UvicornWorker \
    -w 2 --bind 0.0.0.0:8092 \
    --timeout 120 --graceful-timeout 30
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### NGINX (CloudPanel) — Port 8092 + WebSocket + Medien-Auth

```nginx
# Statische Dateien direkt ausliefern (kein Auth erforderlich)
location /static/ {
    alias /home/clp-einsatz/htdocs/einsatzleiter/app/static/;
    expires 7d;
    add_header Cache-Control "public, immutable";
}

# WICHTIG: Medien-Dateien NICHT direkt ausliefern —
# sie liegen außerhalb von app/static und werden nur über
# /medien/datei/{id} mit Auth-Check ausgeliefert.

# WebSocket-Upgrade (zwingend!)
location /ws {
    proxy_pass http://127.0.0.1:8092;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}

# Upload-Größe für Medien (Video bis 50 MB + Overhead)
client_max_body_size 60M;

# Alle anderen Anfragen
location / {
    proxy_pass http://127.0.0.1:8092;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
}
```

### Graceful Reload nach Update

```bash
# Gunicorn neu laden (kein Downtime)
sudo kill -HUP $(cat /run/einsatzleiter.pid)
# oder
sudo systemctl reload einsatzleiter
```

---

## Konfiguration (.env)

```dotenv
# ── Datenbank ──────────────────────────────────────────────────────
DATABASE_URL=mysql+pymysql://einsatzleiter:passwort@127.0.0.1:3306/einsatzleiter

# ── Sicherheit ─────────────────────────────────────────────────────
# Mindestens 32 zufällige Zeichen; nie ins Repository committen!
SECRET_KEY=hier-einen-langen-zufaelligen-string-einsetzen
# SECRET_KEY generieren:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"

COOKIE_SECURE=true    # In Produktion zwingend (HTTPS)

# ── App ────────────────────────────────────────────────────────────
APP_HOST=0.0.0.0
APP_PORT=8092
APP_BASE_URL=https://einsatz.example.com
DEBUG=false

# ── Bootstrap-Admin (nur Erststart relevant) ───────────────────────
BOOTSTRAP_ADMIN_USER=admin
BOOTSTRAP_ADMIN_PASSWORD=   # Leer → wird beim ersten Start zufällig generiert und einmalig geloggt

# ── E-Mail / Passwort-Reset ────────────────────────────────────────
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=einsatz@example.com
SMTP_PASSWORD=smtp-passwort
SMTP_FROM=einsatz@example.com
SMTP_STARTTLS=true

# ── Web-Push (VAPID) ───────────────────────────────────────────────
# Schlüssel generieren: python -m app.cli generate-vapid
VAPID_PRIVATE_KEY=
VAPID_PUBLIC_KEY=
VAPID_CLAIM_EMAIL=admin@example.com

# ── Zeitzone ───────────────────────────────────────────────────────
DEFAULT_TIMEZONE=Europe/Vienna  # Fallback; Orgs können eigene Zeitzone setzen

# ── Media-Upload ───────────────────────────────────────────────────
# Speicherort außerhalb von app/static → Auslieferung nur über /medien/datei/{id}
MEDIA_STORAGE_DIR=app_storage/incident_media
MAX_UPLOAD_BYTES_IMAGE=10485760    # 10 MB
MAX_UPLOAD_BYTES_PDF=20971520      # 20 MB
MAX_UPLOAD_BYTES_VIDEO=52428800    # 50 MB
MEDIA_IMAGE_MAX_WIDTH=1920
MEDIA_IMAGE_MAX_HEIGHT=1080
MEDIA_THUMB_SIZE=240
MEDIA_VIDEO_MAX_HEIGHT=720
FFMPEG_BIN=ffmpeg    # Absoluter Pfad falls nötig: /usr/bin/ffmpeg

# ── In-App-Update ──────────────────────────────────────────────────
UPDATE_ZIP_REQUIRE_HASH=true
```

---

## Datenbank-Migrationen

Das Projekt verwendet **Alembic** für schema-verwaltete Migrationen.

```bash
# Alle ausstehenden Migrationen anwenden
alembic upgrade head

# Aktuelle Version anzeigen
alembic current

# Neue Migration nach Modell-Änderung erzeugen
alembic revision --autogenerate -m "kurze_beschreibung"

# Eine Migration zurückrollen
alembic downgrade -1
```

### Migrations-Chronologie

| Datei | Inhalt |
|-------|--------|
| `0001_initial.py` | Vollständiges Schema v1.0.0 |
| `0002_multiorg_settings_update.py` | Multi-Org-Support, OrgSettings |
| `0003_missing_columns.py` | Fehlende Spalten Nachlieferung |
| `0004_alarm_dispatch.py` | AlarmDispatchVehicle (Ausrückordnung) |
| `0005_user_contact_and_reset.py` | Benutzer-Kontaktfelder, Passwort-Reset |
| `0006_org_timezone.py` | `FireDept.timezone` (IANA) |
| `0007_task_media.py` | `task_media`-Tabelle (Media-Upload) |

**Migrations-Pfad beim Deployment** (Reihenfolge beachten):
- PR 1–3: keine neuen System-Deps
- PR 4: `ffmpeg` + `pillow-heif` + Migration `0007` erforderlich
- `alembic upgrade head` immer vor Neustart ausführen

---

## Frontend-Build

Das Projekt verwendet **Tailwind CSS 3** mit PostCSS. Das fertig gebaute `app/static/css/app.css` ist im Repository enthalten — auf dem Produktionsserver ist **kein Node.js** nötig.

```bash
# Einmaliger Build (vor Commit / Release)
npm run build

# Watch-Modus für Entwicklung
npm run dev
```

`package.json`-Scripts:
```json
{
  "build": "tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify",
  "dev":   "tailwindcss -i app/static/css/input.css -o app/static/css/app.css --watch"
}
```

Tailwind scannt alle Templates und JS-Dateien (`tailwind.config.js`, `content`-Pfade). Nach neuen Tailwind-Klassen in Templates immer `npm run build` ausführen und `app.css` committen.

---

## CLI

Verwaltungs-Kommandos über das eingebaute CLI (`app/cli.py`):

```bash
# Admin-Benutzer anlegen
python -m app.cli create-admin --username admin --password geheimpasswort

# API-Key für Alarmierungssystem erstellen
python -m app.cli create-api-key --label "Alarmierungssystem Leitstelle"

# VAPID-Schlüsselpaar für Web-Push generieren
python -m app.cli generate-vapid
```

### Bootstrap-Admin (Erststart)

Beim allerersten Start ohne Benutzer in der Datenbank wird automatisch ein Admin-Konto angelegt:
- Benutzername: `BOOTSTRAP_ADMIN_USER` (Standard: `admin`)
- Passwort: `BOOTSTRAP_ADMIN_PASSWORD` oder zufällig generiert
- Das generierte Passwort wird **einmalig** in den Logs ausgegeben

Nach dem ersten Login sofort Passwort ändern.

---

## Tests

```bash
# Alle Tests
pytest tests/ -v

# Mit Coverage-Report
pytest tests/ --cov=app --cov-report=html
# → htmlcov/index.html öffnen

# Nur ein Modul
pytest tests/test_media.py -v
```

CI (GitHub Actions): Lint (ruff) + Typecheck (mypy) + pytest mit MariaDB-Service-Container (Python 3.14).

### Test-Struktur

```
tests/
├── conftest.py          # Fixtures: test_db (SQLite in-memory), client (TestClient)
├── test_api.py          # REST-API Endpunkte (Einsatz anlegen, idempotenz)
├── test_breathing.py    # Atemschutz-Zustandsmaschine
└── test_media.py        # Media-Upload, Resize, Delete (neu)
```

---

## Sicherheit

### Authentifizierung & Session

- Passwörter: **bcrypt** (12 Runden)
- Session-Token: signiert mit **itsdangerous** (HMAC-SHA1), Max-Age 24h + Inaktivitäts-Timeout 8h
- Brute-Force-Schutz: 10 Fehlversuche → Konto für 15 Minuten gesperrt
- `COOKIE_SECURE=true` erzwingen in Produktion (HTTPS)

### CSRF

- Custom `CSRFMiddleware` (Phase 7): Double-Submit-Cookie-Pattern
- Alle state-ändernden POST-Requests werden geprüft
- `app/static/js/csrf.js` setzt den CSRF-Token automatisch in HTMX-Header

### Multi-Tenant-Isolation

- Jeder Benutzer gehört einer Organisation (`org_id`) an
- Alle DB-Abfragen filtern nach `org_id` des eingeloggten Benutzers
- Incident-Collaboration via `IncidentOrg`-Junction — nur explizit eingeladene Orgs sehen gemeinsame Einsätze
- System-Admin sieht alles; Org-Admin sieht nur eigene Org

### Medien-Sicherheit

- **Dateien außerhalb von `app/static/`**: `app_storage/incident_media/` — kein direkter HTTP-Zugriff möglich
- **Auslieferung nur über `/medien/datei/{id}`** mit vollständigem Auth- und Org-Check → 401/403 ohne Login
- **MIME-Validierung via `filetype`**: User-supplied `Content-Type` wird ignoriert; echte Datei-Bytes werden geprüft
- **Dateinamen**: Gespeichert nur als DB-Feld (`original_filename`), nie im Dateisystem — UUID-Dateinamen verhindern Path-Traversal
- **Pillow-Re-Encode**: HEIC/JPG mit eingeschleustem Polyglot-Payload werden durch Re-Encoding neutralisiert
- **Größenlimits** auf FastAPI-Ebene: 10 MB Bild · 20 MB PDF · 50 MB Video (+ NGINX `client_max_body_size`)

### Rate-Limiting

- **slowapi**: Standard 300 Req/min für alle Endpoints
- Login und Passwort-Reset: eigene engere Limits per Decorator

### API-Key-Sicherheit

- Keys werden als **SHA-256-Hash** gespeichert (nie im Klartext)
- Vergleich via `hmac.compare_digest` (timing-sicher)
- Ablaufdatum + Revoke-Funktion vorhanden

---

## Architektur

```
┌─────────────────────────────────────────────────────────┐
│                      Browser / PWA                       │
│  HTMX (Form-Submits + partielle DOM-Updates)            │
│  Alpine.js (lokaler State: Modals, Toasts, Lightbox)    │
│  SortableJS (Drag & Drop)                               │
│  WebSocket (Echtzeit-Events)                            │
└────────────────────────────┬────────────────────────────┘
                             │ HTTP / WS
┌────────────────────────────▼────────────────────────────┐
│                   NGINX (Reverse Proxy)                  │
│  /static/ → direkt (7d Cache)                          │
│  /ws      → WebSocket-Upgrade                          │
│  /        → Proxy → :8092                              │
│  client_max_body_size 60M (Media-Upload)               │
└────────────────────────────┬────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────┐
│           FastAPI (app.main) auf Port 8092               │
│  Gunicorn + UvicornWorker (2 Workers)                  │
│                                                         │
│  Middleware-Stack:                                      │
│    SessionMiddleware → SecurityHeaders → CSRF           │
│    SlowAPI (Rate-Limit)                                │
│                                                         │
│  Routers:                                              │
│    ui_incident  – Board, Aufgaben, Fahrzeuge           │
│    ui_media     – Galerie, /medien/datei/{id}          │
│    ui_breathing – Atemschutzüberwachung                │
│    ui_archive   – Archiv, PDF-Export                   │
│    ui_admin     – Stammdaten, Benutzer                 │
│    ui_settings  – Org-Settings, ZIP-Update             │
│    ui_stats     – Statistik                            │
│    api_v1       – REST-API (Alarmierung)               │
│    ws           – WebSocket Pub/Sub                    │
│    auth         – Login/Logout/Reset                   │
│                                                         │
│  Services:                                             │
│    incident_service  – Einsatz-Logik                   │
│    media_service     – Upload-Pipeline (Bild/PDF/Video)│
│    pdf_service       – WeasyPrint PDF                  │
│    push_service      – Web-Push VAPID                  │
│    broadcast         – WS-Pub/Sub-Manager              │
│    update_service    – ZIP-Update + Alembic            │
└────────────────────────────┬────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
┌─────────────▼──┐  ┌───────▼──────┐  ┌───▼──────────────┐
│  MariaDB 10.11 │  │ app_storage/ │  │ app/static/      │
│  (utf8mb4)     │  │ incident_    │  │ css, js, img     │
│                │  │ media/       │  │ (kein Auth nötig)│
│  Tabellen:     │  │ (Auth-Schutz)│  └──────────────────┘
│  incident      │  └─────────────┘
│  task          │
│  task_media    │
│  user          │
│  fire_dept     │
│  breathing_*   │
│  ...           │
└────────────────┘
```

### Datenfluss: Media-Upload

```
Browser                  FastAPI                 Dateisystem + DB
  │                         │                         │
  │─── POST /aufgabe/{id}/medien (multipart) ────────►│
  │                         │                         │
  │                    filetype.guess()               │
  │                    Größen-Check                   │
  │                    Pillow / ffmpeg                │
  │                         │──── schreibt UUID.jpg ──►│
  │                         │──── schreibt UUID_thumb ►│
  │                         │──── INSERT task_media ──►│
  │                         │                         │
  │◄── 200 _task_media.html (HTMX-Partial) ──────────│
  │    (DOM-Swap #taskMediaSection)                   │
```

---

## REST-API

### Einsatz anlegen (Alarmierungssystem)

```http
POST /api/v1/einsatz
X-API-Key: elh_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json
```

```json
{
  "Key": "426747e9-0126-45bc-a0c1-b51a182de14b",
  "Nummer": 1978,
  "AlarmDatumZeit": "2026-05-19T21:11:11.323",
  "Stufe": "t3",
  "Art": "T",
  "Meldung": "Wolfurt Senderstraße 34 Heizraum überflutet",
  "Einsatzgrund": "Heizraum überflutet",
  "Ort": "Wolfurt",
  "Strasse": "Senderstraße",
  "HausNr": "34",
  "Uebung": false
}
```

**Idempotenz:** Doppelter `Key` → `created: false`, vorhandene `incident_id` wird zurückgegeben.

API-Key erstellen:
```bash
python -m app.cli create-api-key --label "Alarmierungssystem"
```

---

## Rollen-System

| Rolle | Code | Bereich | Berechtigungen |
|-------|------|---------|----------------|
| **Systemadmin** | `system_admin` | Systemweit | Alles, alle Orgs; kann Einsätze endgültig löschen |
| **Org-Admin** | `org_admin` / `admin` | Eigene Org | Vollzugriff innerhalb der Org |
| **Einsatzleiter** | `incident_leader` | Einsatz | Board bearbeiten, Atemschutz steuern |
| **AS-Überwacher** | `breathing_supervisor` | Atemschutz | Nur Atemschutzüberwachung |
| **Schriftführer** | `recorder` | Einsatz | Journal, Meldungen, Media-Upload |
| **Beobachter** | `readonly` | Einsatz | Nur Lesen |

---

## KI-Assistent (✨)

Der optionale KI-Assistent nutzt die **Anthropic Claude API** und muss pro Instanz explizit aktiviert werden.

### Aktivierung

In den System-Einstellungen (`/admin/system-einstellungen`, nur `system_admin`):
- `ai_enabled = true`
- `ai_api_key = sk-ant-...`

Default: **deaktiviert** (`AI_ENABLED=false`).

### Funktionen

| Funktion | Beschreibung |
|----------|-------------|
| **✨ Auftragsvorschläge** | Beim Alarmeingang (via REST-API) generiert die KI 3–5 Erstmaßnahmen als Kanban-Tasks mit `source="ai_suggestion"`. Der Einsatzleiter bestätigt (✓) oder verwirft (✗) jeden Vorschlag. |
| **✨ Lage-Hinweise** | Taktische Ticker-Hinweise für das Board (Sidebar + Header). KI-Hinweise werden mit ✨ markiert; admin-gepflegte Hinweise dienen als Fallback wenn keine KI-Hinweise vorliegen. |
| **✨ Lagebild** | Kompakte Lagebeschreibung aus Live-Einsatzdaten; kann ins Journal übernommen werden. |
| **✨ Einsatzbericht** | KI-Entwurf für den Abschlussbericht (im Archiv). |
| **✨ Auto-Priorisierung (Großschadenslage)** | Beim Anlegen einer Einsatzstelle (manuell, API, Bürgermeldung) analysiert die KI die Einsatzmeldung und schlägt automatisch eine Priorität (Sofort / Dringend / Normal / Aufschiebbar) samt `danger_score` und `urgency_score` vor. |

### Auftragsvorschlags-Chips

Im "Auftrag anlegen"-Dialog werden Vorschläge als Chips dargestellt:
- **KI-Vorschläge vorhanden** → nur KI-Chips (✨) werden angezeigt
- **Keine KI-Vorschläge** → admin-gepflegte Vorlagen als Fallback
- Das ✨-Icon erscheint nur im Chip-Label — beim Übernehmen wird der reine Auftragstext ohne ✨ eingesetzt

### Sicherheit & Datenschutz

- **Keine Personendaten an die KI**: Alle Payloads durchlaufen `_strip_persons()` in `ai_service.py`
- **KI ist Assistenz, nie Akteur**: Das LLM erzeugt nur Vorschläge — keine automatischen Statusänderungen
- **Alarm-Anlage scheitert nie an KI-Fehler**: AI-Enrichment läuft als Background-Task, Fehler werden geloggt
- Tests mocken den Provider: `ai_service.complete` via Monkeypatching in `conftest.py`

---

## Einsätze löschen (system_admin)

System-Admins können Einsätze im Archiv endgültig löschen:

1. Archiv-Detailseite des Einsatzes öffnen (`/archiv/{id}`)
2. Button **🗑 Löschen** anklicken (nur für `system_admin` sichtbar)
3. Doppelte Bestätigungsabfrage im Browser
4. Alle abhängigen Daten (Aufgaben, Meldungen, Fahrzeuge, Atemschutz, Journal, Medien) werden gelöscht

Die Aktion wird im Audit-Log protokolliert (`admin.incident.deleted`).

---

## Multi-Organisations-Architektur

Mehrere Feuerwehren können auf einer Instanz betrieben werden und gemeinsam an Einsätzen arbeiten.

```
System-Admin (organisationsübergreifend)
    │
    ├── Organisation A (z. B. FF Wolfurt) — Org-Admin A
    │   ├── Benutzer von Org A
    │   ├── Mitglieder, Fahrzeuge, Stammdaten
    │   └── Einstellungen (Logo, Farbe, Zeitzone)
    │
    └── Organisation B (z. B. FF Lauterach) — Org-Admin B
        └── ...

Gemeinsamer Einsatz:
    Org A erstellt Einsatz → lädt Org B ein (IncidentOrg)
    → Benutzer beider Orgs sehen & bearbeiten den Einsatz
    → Fahrzeuge + Mitglieder beider Orgs verfügbar
    → Media-Galerie isoliert: jede Org sieht nur eigene Medien
```

---

## In-App ZIP-Update

Updates können über die Weboberfläche eingespielt werden — kein SSH erforderlich.

**Ablauf** (`/admin/system/update`, nur `system_admin`):
1. Release-ZIP hochladen (muss `app/` und `pyproject.toml` enthalten)
2. Optional: SHA-256-Prüfsumme eingeben (Empfehlung: immer verwenden)
3. System validiert ZIP (Zip-Slip-Schutz), extrahiert in tmp, kopiert Dateien
4. Führt `alembic upgrade head` aus
5. Sendet SIGHUP an Gunicorn (graceful reload, kein Downtime)

**Geschützte Pfade** (werden nie überschrieben):

| Pfad | Grund |
|------|-------|
| `.env` | Secrets |
| `alembic/versions/` | Eigene Migrationen |
| `app/static/img/uploads/` | Hochgeladene Logos |
| `app_storage/` | Medien-Dateien |

**Release-ZIP erstellen:**
```bash
git archive --format=zip --prefix=release-2.0.0/ HEAD > release-2.0.0.zip
sha256sum release-2.0.0.zip   # Prüfsumme notieren
```

---

## Projektstruktur

```
app/
├── main.py              FastAPI-App, Middleware, Router-Registrierung
├── config.py            Einstellungen (pydantic-settings, .env)
├── db.py                SQLAlchemy-Engine, SessionLocal, Base
├── cli.py               CLI: create-admin, create-api-key, generate-vapid
├── seed_data.py         Initialdaten (Rollen, Alarmtypen, ...)
├── core/
│   ├── security.py      Passwort-Hashing, Session-Signing, QR-Token
│   ├── permissions.py   require_role(), has_role()
│   ├── templating.py    Jinja2-Environment + Zeitzonen-Filter
│   └── audit.py         Audit-Log-Helfer
├── middleware/
│   ├── security_headers.py  CSP, X-Frame-Options, ...
│   └── csrf.py              Double-Submit CSRF-Schutz
├── models/
│   ├── incident.py      Incident, Task, TaskMedia, Message, ...
│   ├── user.py          User, Role, ApiKey, AuditLog, ...
│   ├── master.py        FireDept, VehicleMaster, Member, OrgSettings, ...
│   ├── breathing.py     BreathingTroop, TroopMember, PressureLog
│   └── password_reset.py
├── routers/
│   ├── ui_incident.py   Board, Aufgaben, Fahrzeuge, Media-Upload
│   ├── ui_media.py      Galerie (/medien), geschützte Datei-Auslieferung
│   ├── ui_breathing.py  Atemschutzüberwachung
│   ├── ui_archive.py    Archiv, PDF-Export
│   ├── ui_admin.py      Stammdaten, Benutzer, API-Keys, Audit
│   ├── ui_settings.py   Org-Einstellungen, ZIP-Update, System-Admin
│   ├── ui_stats.py      Statistik-Dashboard
│   ├── ui_push.py       Web-Push-Verwaltung
│   ├── ui_password_reset.py
│   ├── api_v1.py        REST-API (Alarmierung)
│   ├── ws.py            WebSocket Pub/Sub
│   └── auth.py          Login / Logout
├── services/
│   ├── incident_service.py  Einsatz-Logik, Spalten, Tasks
│   ├── media_service.py     Upload-Pipeline (Bild/PDF/Video/HEIC)
│   ├── pdf_service.py       WeasyPrint PDF-Generierung
│   ├── push_service.py      Web-Push (VAPID)
│   ├── broadcast.py         WS-Pub/Sub-Manager
│   ├── mail_service.py      SMTP (Passwort-Reset)
│   └── update_service.py    ZIP-Update + Alembic-Migration
├── static/
│   ├── css/app.css          Fertiger Tailwind-Build (committet)
│   ├── js/                  alpine.min.js, htmx.min.js, sortable.min.js,
│   │                        app.js, sortable-glue.js, lightbox.js, ...
│   └── img/                 Logo, Favicon, Icons
└── templates/
    ├── base.html            Master-Layout (Nav, Modal, Toasts, WS-Alert)
    ├── incident/            Board-Komponenten, Task/Fahrzeug-Modals
    ├── media/               gallery.html
    ├── admin/, archive/, auth/, breathing/, pdf/, stats/
alembic/versions/            Migrations (0001–0007)
deploy/                      systemd-Service, NGINX-Snippet
tests/                       pytest-Suite (conftest, test_api, test_media, ...)
app_storage/incident_media/  Medien-Dateien (Auth-geschützt, nicht im Repo)
```

---

## Autoren

| Name | Rolle |
|------|-------|
| **Johannes Battlogg** ([@BattloXX](https://github.com/BattloXX)) | Lead-Entwicklung, Konzept & Design |
| **Roman Reiter** | Fachberatung Einsatzleitung & Atemschutz |

---

## Versionshistorie

| Version | Datum | Highlights |
|---------|-------|------------|
| **2.0.0** | 2026-05-23 | Media-Upload + Galerie, Multi-Org, System-Admin-Rolle, Zeitzone je Org, ZIP-Update, Python 3.14 |
| **1.0.0** | 2026-05-22 | Initiale Webapp (FastAPI + HTMX, WebSocket, Atemschutz, PWA, QR-Code) |

---

## Lizenz

MIT License — Freiwillige Feuerwehr Wolfurt  
Nutzung für alle österreichischen Feuerwehren ausdrücklich erwünscht.
