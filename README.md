# Einsatzleiter-Hilfswerkzeug

**Digitales Einsatzleiter-Werkzeug für Feuerwehren** — Multi-User, Multi-Organisations-fähig, Echtzeit.


[![CI](https://github.com/BattloXX/Einsatzleiter-Hilfswerkzeug/actions/workflows/ci.yml/badge.svg)](https://github.com/BattloXX/Einsatzleiter-Hilfswerkzeug/actions)
![Python](https://img.shields.io/badge/python-3.14-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green)
![Version](https://img.shields.io/badge/version-2.5.0-orange)

---

## Überblick

Das Werkzeug ersetzt ein Single-File-HTML-Tool durch eine vollwertige Webapp, die Einsatzleitern und Schriftführern eine strukturierte, Echtzeit-fähige Arbeitsumgebung bietet.

**Zielgruppe:** Einsatzleiter, Schriftführer und Atemschutz-Überwacher österreichischer Feuerwehren.

**Kern-Prinzipien:**
- Mehrere Geräte (Tablet, PC, Mobilgerät) arbeiten gleichzeitig am selben Einsatz
- Vollständiges Audit-Log — jede Änderung wird protokolliert (Zeitreise-Funktion)
- Multi-Tenancy — mehrere Organisationen auf einer Instanz, row-level isoliert
- Offline-fähige PWA — eingeschränkte Nutzung auch ohne Netzverbindung

---

## Features

| Feature | Beschreibung |
|---------|-------------|
| **Echtzeit-Kanban-Board** | WebSocket-basiertes Board für Einsatzkräfte, Aufgaben, Fahrzeuge und Meldungen |
| **Atemschutzüberwachung** | Rückzugsdruckberechnung, Zeitmessung, gesetzlich verpflichtend |
| **Media-Galerie** | Bilder, PDFs und Videos direkt an Aufträge anhängen; sichere Auslieferung über Auth-Route |
| **Multi-Org-Support** | Mehrere Feuerwehren, gemeinsame Einsätze, eigene Stammdaten je Org; row-level isoliert via SQLAlchemy Event-Handler |
| **REST-API** | Automatische Einsatzanlage aus dem Alarmierungssystem (idempotent); Rate-Limiting per API-Key |
| **PDF-Export** | Einsatzbericht mit Zeitstempeln, Audit-Log, Geretteten als WeasyPrint-PDF |
| **Archiv & Zeitreise** | Vollständiges Änderungsprotokoll; jeden Zustand in der Vergangenheit anzeigen |
| **Web-Push-Benachrichtigungen** | PWA Push bei neuem Einsatz (VAPID) |
| **QR-Code-Login** | Schnell-Login für Tablet-Stationen ohne Passwort-Eingabe |
| **In-App ZIP-Update** | Neue Version per Upload einspielen, kein SSH erforderlich |
| **Statistik-Dashboard** | Einsatzauswertung nach Typ, Zeit, Fahrzeug |
| **Stammdaten-Verwaltung** | Fahrzeuge, Mitglieder, Qualifikationen (AGT-Ablaufdaten), Alarmtypen |
| **Großschadenslage** | Phasen-Kanban für Massenanfall-Ereignisse: Einsatzstellen, Abschnitte, Stabsfunktionen (SKKM), Einsatzjournal, Funkjournal, Bürgermeldungen, Pressemeldung; QR-Code-Schnellzugang |
| **Großschadenslage-Karte** | Interaktive Lagekarte (Leaflet) mit Abschnitt-Polygonen (live, kein Reload), Pin-Modus per Kartenklick mit Reverse Geocoding, Marker-Clustering, Fahrzeug-GPS-Live-Tracking |
| **Taktischer Anzeigemodus (ÖBFV E-27)** | Lagekarte nach ÖNORM-Einsatztaktik: genormte taktische Symbole, Magnetfarben je Einheitstyp, einblendbare Legende |
| **Lagekarte-Druck & Print-Center** | Druckdialog A4/A3 (Lagekarte vs. Lagebericht), interaktive Druckvorschau mit Zoom/Pan-Formatrahmen, Druckfußzeile mit Logo, Zeiten, Einsatzstellen-Statistik & Ressourcen; Mehrfachauswahl zum Sammeldruck |
| **GSL-Ressourcenverwaltung** | Einheiten anlegen, filtern, per Drag & Drop sortieren; direkt Einsatzstellen zuordnen (Board-Karte, Detail-Panel, Verlauf); Mehrfach-Disposition; Fremdorganisations-Ressourcen; eigenes Ressourcen-Journal |
| **SKKM-Lagemeldungs-Regelkreis** | Lage → Auftrag → Kontrolle: Fälligkeits-Timer je Einsatzstelle, automatischer Auftrag im Funkjournal bei Überfälligkeit, Live-Chip |
| **Übergreifende Meldungen** | Cross-Marker mit Status-Workflow, Notizen & Medien, Kamera-/Galerie-Upload, OSM-Karte (Org-Standort), Bearbeiten & Drucken |
| **Einsatzkarte (Detail-Panel)** | Live-Updates ohne Reload, Kräfteübersicht, Foto-Upload (Kamera/Galerie) mit Lightbox, Karten-Pin, Druck |
| **SMS-Gateway-Anbindung** | Docker-Container verbindet sich ausgehend über WebSocket und versendet SMS via CoNiuGo-Modem; SMS-Verifikation der Telefonnummer im Bürgerportal |
| **KI-Assistent (✨)** | Auftragsvorschläge, Lage-Ticker-Hinweise, Lagebild und automatische Priorisierung via Anthropic Claude; opt-in pro Org |
| **Org-Konfig-Backup** | JSON-Export/Import der Org-Konfiguration inkl. Dry-Run-Diff |
| **System-Admin-Konsole** | Per-Org KPI-Übersicht mit Schnellzugriff für Systemadministratoren |
| **Auto-Schließen** | Inaktive Einsätze werden nach konfigurierbarer Zeit automatisch geschlossen (systemweit und pro Org) |
| **Wetterdaten-Integration** | Echtzeit-Nowcast (15-min), Ist-Werte, +6/+12/+24h-Vorhersage und Unwetterwarnungen; Kachelmann Plus-API als Primärquelle mit GeoSphere Austria/ZAMG-Ergänzung und Open-Meteo-Fallback; Sturm- und Waldbrand-Szenario-Indikatoren; Radar-Overlay (RainViewer) auf der Lagekarte; globale `/wetter`-Seite; opt-out je Org |
| **Benutzer-Profil** | Eigener Name, E-Mail, Passwort und Avatar; Profilbild erscheint in Log-Einträgen und Stab |

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
| Karten | **Leaflet** + **Leaflet-Geoman** (Polygon-Zeichnen) + **Leaflet.markercluster** |
| Rich-Text-Editor | **Quill** (Einsatzjournal, Stabsmeldungen) |
| CSS-Framework | **Tailwind CSS 3** (lokaler Build, kein CDN) |
| Realtime | FastAPI **WebSockets** (Pub/Sub je Einsatz) |
| Auth | Session-Cookies + **bcrypt** + **itsdangerous** |
| PDF-Erzeugung | **WeasyPrint** |
| Web-Push | **pywebpush** (VAPID) |
| Bild-Verarbeitung | **Pillow** + **pillow-heif** (HEIC/iPhone) |
| Video-Transcode | **ffmpeg** (subprocess, H.264/AAC, 720p) |
| PDF-Metadaten | **pypdf** (Seitenanzahl) |
| Rate-Limiting | **slowapi** (IP-basiert + API-Key-basiert) |
| QR-Code | **qrcode[pil]** |
| PWA | Service Worker + Web App Manifest |
| HTTP-Client (async) | **httpx** (Kachelmann, GeoSphere Austria & Open-Meteo Weather-APIs) |
| Wetter-Daten | **Kachelmann Plus-API** (Primärquelle) + **GeoSphere Austria Data Hub** / ZAMG (CC BY 4.0) + **Open-Meteo** (Fallback) + **RainViewer** (Radar) |
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

Vollständige Anleitung: [`docs/wiki/Installation-App-Installation.md`](docs/wiki/Installation-App-Installation.md)

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
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

### Graceful Reload nach Update

```bash
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
PUBLIC_BASE_URL=         # Für Mail-Links; leer = APP_BASE_URL verwenden
DEBUG=false
# Testsystem-Modus: zeigt "TEST SYSTEM" im Header und auf allen Ausdrucken
TEST_SYSTEM=false

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

# ── Rate-Limiting ──────────────────────────────────────────────────
LOGIN_RATELIMIT=10/minute          # POST /login
API_ALARM_RATELIMIT=60/minute      # POST /api/v1/einsatz (Key-basiert)
UPLOAD_RATELIMIT=20/minute         # Medien-Upload

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

# ── KI-Assistent ───────────────────────────────────────────────────
ANTHROPIC_API_KEY=          # Überschreibt DB-Wert; leer = nur DB-Wert
AI_ENABLED=false
AI_MODEL_DEFAULT=claude-sonnet-4-6
AI_MODEL_FAST=claude-haiku-4-5-20251001

# ── In-App-Update ──────────────────────────────────────────────────
UPDATE_ZIP_REQUIRE_HASH=true

# ── Wetter ─────────────────────────────────────────────────────────
WEATHER_ENABLED=true
# Kachelmann Plus-API (kostenpflichtig) — Primärquelle wenn Key gesetzt.
# Bevorzugt in den Systemeinstellungen pflegen (kachelmann_api_key); ENV = Fallback.
KACHELMANN_API_KEY=
# KACHELMANN_BASE_URL=https://api.kachelmannwetter.com/v02  # default
# GeoSphere Austria / ZAMG (CC BY 4.0, kein API-Key) — Standardquelle/Ergänzung
# GEOSPHERE_BASE_URL=https://dataset.api.hub.geosphere.at/v1  # default
# GEOSPHERE_WARN_URL=https://warnungen.zamg.at/wsapp/api      # amtliche Warnungen
WEATHER_CACHE_TTL_NOWCAST=300    # Sekunden; Nowcast + Ist-Werte
WEATHER_CACHE_TTL_NWP=1800       # Sekunden; NWP-Vorhersage
WEATHER_CACHE_TTL_WARN=300       # Sekunden; Unwetterwarnungen
WEATHER_HTTP_TIMEOUT=8           # Sekunden; externe API-Anfragen
WEATHER_RADIUS_KM=15             # Fokus-Radius für Radarkarte
WEATHER_FALLBACK_OPENMETEO=true  # Open-Meteo als Fallback wenn Primärquelle nicht erreichbar
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

### Migrations-Chronologie (Auszug)

| Datei | Inhalt |
|-------|--------|
| `0001_initial.py` | Vollständiges Schema v1.0.0 |
| `0044_multitenancy_pr1_infrastructure.py` | TenantScoped-Infrastruktur, org_id-Felder |
| `0045–0047_multitenancy_pr2_*` | AlarmType-Migration (Expand/Migrate/Contract) |
| `0048–0050_multitenancy_pr3_*` | Stammdaten org-scopen |
| `0051_multitenancy_pr4_seed_templates.py` | Seed-Vorlagen |
| `0052_multitenancy_pr5_ai_per_org.py` | KI-Prompts je Org |
| `0053_multitenancy_pr6_storage_quota.py` | Speicher-Quotas |
| `0054_multitenancy_pr7_invitations.py` | Einladungsmodell, QR-Tokens |
| `0055_multitenancy_pr8_autoclose_backup.py` | Auto-Schließen je Org |
| `0065_vehicle_position.py` | Fahrzeug-GPS-Positionshistorie |
| `0066_weather_enabled.py` | `weather_enabled`-Flag in `OrgSettings` (Wetter-Opt-out je Org) |
| `0067–0071_*` | Lagekarte-Persistenz, übergreifende Meldungen, GSL-Ressourcen (PR 1–5), LageToken |
| `0072–0074_lage_einheit_*` | Disponier-Felder, Einheit-Einsatzleiter, GSL-Leiter-Historie |
| `0076_rename_sichter_to_erkunder.py` | Stabsrolle „Sichter" → „Erkunder" |
| `0077_gsl_lagemeldung_regelkreis.py` | SKKM-Lagemeldungs-Regelkreis (Timer-Felder, `auto_kind`) |
| `0078_external_resources.py` | Fremdorganisations-Ressourcen |
| `0079_multi_site_dispatch.py` | Mehrfach-Disposition von Einheiten an Einsatzstellen |

Vollständiger Migrationsleitfaden: [`docs/MIGRATION_RUNBOOK.md`](docs/MIGRATION_RUNBOOK.md)

---

## Frontend-Build

Das Projekt verwendet **Tailwind CSS 3** mit PostCSS. Das fertig gebaute `app/static/css/app.css` ist im Repository enthalten — auf dem Produktionsserver ist **kein Node.js** nötig.

```bash
# Einmaliger Build (vor Commit / Release)
npm run build

# Watch-Modus für Entwicklung
npm run dev
```

---

## CLI

Verwaltungs-Kommandos über das eingebaute CLI (`app/cli.py`):

```bash
# Admin-Benutzer anlegen
python -m app.cli create-admin --username admin --password geheimpasswort

# API-Key für Alarmierungssystem erstellen (mit Org-Zuordnung)
python -m app.cli create-api-key --label "Alarmierungssystem Leitstelle" --org-id 1

# Connection-Token für SMS-Gateway-Container erstellen
python -m app.cli create-sms-gateway-token --label "Modem Wolfurt" --org-id 1

# VAPID-Schlüsselpaar für Web-Push generieren
python -m app.cli generate-vapid
```

### Bootstrap-Admin (Erststart)

Beim allerersten Start ohne Benutzer in der Datenbank wird automatisch ein Admin-Konto angelegt:
- Benutzername: `BOOTSTRAP_ADMIN_USER` (Standard: `admin`)
- Passwort: `BOOTSTRAP_ADMIN_PASSWORD` oder zufällig generiert
- Das generierte Passwort wird **einmalig** in den Logs ausgegeben

Nach dem ersten Login sofort Passwort ändern und `system_admin`-Rolle setzen.

---

## Tests

```bash
# Alle Tests
pytest tests/ -v

# Nur Unit-Tests (ohne DB-Verbindung)
pytest tests/test_breathing.py tests/test_api_hardening.py tests/test_isolation.py \
       tests/test_autoclose_per_org.py tests/test_sysadmin.py tests/test_smoke.py -v

# Mit Coverage-Report
pytest tests/ --cov=app --cov-report=html
```

CI (GitHub Actions): Lint (ruff) + Typecheck (mypy) + pytest mit MariaDB-Service-Container (Python 3.14).

### Test-Struktur

**26 Testmodule, 335+ Testfunktionen.** Auswahl:

```
tests/
├── conftest.py                 Fixtures: test_db (SQLite in-memory), client, API-Key
├── test_api.py                 REST-API Endpunkte (Einsatz anlegen, Idempotenz)
├── test_api_hardening.py       AlarmPayload/LageAlarmPayload Validation + Rate-Limit-Key
├── test_breathing.py           Atemschutz-Zustandsmaschine
├── test_isolation.py           Multi-Tenancy Row-Level-Isolation + can_access_incident
├── test_tenant_isolation.py    Tenant-Kontext fail-closed
├── test_visibility_matrix.py   Sichtbarkeits-Testmatrix je Rolle
├── test_autoclose_per_org.py   Auto-Schließen: global vs. org-spezifisch
├── test_sysadmin.py            _org_stats() Aggregation (System-Admin-Konsole)
├── test_stats_scoping.py       Statistik-Aggregate org-gescoped
├── test_storage_quota.py       Speicher-Quotas je Org
├── test_invitation.py          Einladungsmodell
├── test_lagekarte_api.py       Lagekarte, Abschnitte, Marker
├── test_lagemeldung_service.py SKKM-Lagemeldungs-Regelkreis (Timer-Logik)
├── test_multi_site_dispatch.py Mehrfach-Disposition von Einheiten
├── test_weather_service.py     Wetter-Aggregation + Fallback
├── test_kachelmann_service.py  Kachelmann Plus-API
├── test_weather_focus.py       Sturm-/Waldbrand-Szenario-Analyse
├── test_address_autocomplete.py / test_address_edit.py  Adress-Suche & -Bearbeitung
├── test_ai_*.py                KI: Vorschläge, Lagebild, Bericht, Service
└── test_smoke.py               Import-Smoke-Tests
```

---

## Sicherheit

### Authentifizierung & Session

- Passwörter: **bcrypt** (12 Runden)
- Session-Token: signiert mit **itsdangerous** (HMAC-SHA1), Max-Age 24h + Inaktivitäts-Timeout 8h (Sliding Window)
- Brute-Force-Schutz: konfigurierbare Anzahl Fehlversuche → Konto für konfigurierbare Dauer gesperrt (`LOGIN_MAX_FAILED`, `LOGIN_LOCKOUT_MINUTES`)
- `COOKIE_SECURE=true` erzwingen in Produktion (HTTPS)

### CSRF

- `CSRFMiddleware`: Double-Submit-Cookie-Pattern
- Alle state-ändernden POST-Requests werden geprüft
- `app/static/js/csrf.js` setzt den CSRF-Token automatisch in HTMX-Header

### Multi-Tenant-Isolation

- Jeder Benutzer gehört einer Organisation (`org_id`) an
- DB-Abfragen mit `db.query()` filtern via SQLAlchemy `do_orm_execute`-Event automatisch nach `org_id`
- `db.get()` umgeht den Event-Handler — Router prüfen daher nach Laden manuell via `same_org_or_system_admin()`
- Incident-Collaboration via `IncidentOrg`-Junction — nur explizit eingeladene Orgs sehen gemeinsame Einsätze (`visible_incidents_q()`)
- System-Admin sieht alles; Org-Admin sieht nur eigene Org

### Medien-Sicherheit

- **Dateien außerhalb von `app/static/`**: `app_storage/incident_media/` — kein direkter HTTP-Zugriff möglich
- **Auslieferung nur über `/medien/datei/{id}`** mit vollständigem Auth- und Org-Check
- **MIME-Validierung via `filetype`**: echte Datei-Bytes werden geprüft
- **Dateinamen**: UUID-basiert im Dateisystem — verhindert Path-Traversal

### Rate-Limiting (slowapi)

- Standard: 300 Req/min für alle Endpoints
- `POST /login`: `LOGIN_RATELIMIT` (Standard: 10/min) — IP-basiert
- `POST /api/v1/einsatz`: `API_ALARM_RATELIMIT` (Standard: 60/min) — **API-Key-basiert** (je Key ein eigenes Limit-Budget)
- Medien-Upload: `UPLOAD_RATELIMIT` (Standard: 20/min) — IP-basiert

### API-Key-Sicherheit

- Keys werden als **SHA-256-Hash** gespeichert (nie im Klartext)
- Vergleich via `hmac.compare_digest` (timing-sicher)
- Ablaufdatum + Revoke-Funktion; pro Org isoliert

---

## Architektur

```
┌─────────────────────────────────────────────────────────┐
│                      Browser / PWA                       │
│  HTMX · Alpine.js · SortableJS · WebSocket              │
└────────────────────────────┬────────────────────────────┘
                             │ HTTP / WSS
┌────────────────────────────▼────────────────────────────┐
│                   NGINX (Reverse Proxy)                  │
│  /static/ → direkt · /ws → WS-Upgrade · / → :8092      │
└────────────────────────────┬────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────┐
│           FastAPI (app.main) auf Port 8092               │
│                                                         │
│  Middleware: SessionMiddleware → SecurityHeaders         │
│             → CSRF → SlowAPI (Rate-Limit)               │
│                                                         │
│  Routers:                                               │
│    ui_incident      – Board, Aufgaben, Fahrzeuge        │
│    ui_major_incident– Großschadenslage, Einsatzkarte    │
│    ui_gsl_staff     – Stab, SKKM-Einsatzjournal         │
│    lagekarte_api    – Lagekarte, Abschnitte, Marker     │
│    ui_weather       – Wetter-Panel, /wetter             │
│    ui_media         – Galerie, /medien/datei/{id}       │
│    ui_breathing     – Atemschutzüberwachung             │
│    ui_archive       – Archiv, PDF-Export                │
│    ui_admin         – Stammdaten, Benutzer, Audit       │
│    ui_settings      – Org-Einstellungen, ZIP-Update     │
│    ui_backup        – Konfig-Export/Import              │
│    ui_sysadmin      – System-Admin-Konsole              │
│    ui_invitation    – Einladungslinks                   │
│    ui_ai_prompts    – KI-Prompt-Verwaltung              │
│    ui_profile       – Benutzer-Profil (Name/Avatar)     │
│    ui_stats         – Statistik                         │
│    ui_push          – Web-Push-Verwaltung               │
│    public           – Bürger-Meldeportal (öffentlich)   │
│    api_v1           – REST-API (Alarmierung, Lage)      │
│    device_api       – SMS-Gateway-/Geräte-Anbindung     │
│    ws               – WebSocket Pub/Sub                 │
│    auth             – Login/Logout/QR-Login             │
│                                                         │
│  Core:                                                  │
│    security.py   – Passwort, Session, API-Key, QR       │
│    permissions.py – require_role, has_role,             │
│                     can_access_incident                 │
│    queries.py    – visible_incidents_q (Tenant-Filter)  │
│    rate_limit.py – slowapi Limiter + API-Key-Identifier │
│    audit.py      – Audit-Log-Writer                     │
│    middleware/   – CSP-Headers, CSRF                    │
│                                                         │
│  Services:                                              │
│    incident_service      – Einsatz-Logik                │
│    major_incident_service– Großschadenslage             │
│    resource_service      – GSL-Ressourcen/Disposition   │
│    lagekarte             – Lagekarte-Persistenz          │
│    lagemeldung_service   – SKKM-Regelkreis-Timer         │
│    gsl_lagemeldung_remind– Auto-Auftrag bei Überfäll.    │
│    gsl_staff_service     – Stab/Einsatzjournal           │
│    weather_service       – Wetter-Aggregation            │
│    kachelmann_service    – Kachelmann Plus-API           │
│    weather_focus         – Sturm-/Waldbrand-Szenario     │
│    geocoding/geo_service – Adresse↔Koordinaten           │
│    media_service         – Upload-Pipeline               │
│    lage_media_service    – GSL-Medien                    │
│    pdf_service           – WeasyPrint PDF                │
│    push_service          – Web-Push VAPID                │
│    broadcast             – WS-Pub/Sub-Manager            │
│    autoclose             – Auto-Schließen-Job            │
│    task_reminder         – Auftrags-/Meldungs-Reminder   │
│    ai_service            – Anthropic Claude              │
│    alarm_service         – Alarmtyp-Lookup               │
│    sms_service           – SMS-Gateway-Versand           │
│    seed_service          – Seed-Template-Anwendung       │
└────────────────────────────┬────────────────────────────┘
              ┌──────────────┼──────────────┐
┌─────────────▼──┐  ┌───────▼──────┐  ┌───▼──────────────┐
│  MariaDB 10.11 │  │ app_storage/ │  │ app/static/      │
│  (utf8mb4)     │  │ incident_    │  │ css, js, img     │
│  79 Migrationen│  │ media/       │  │ (kein Auth nötig)│
└────────────────┘  └─────────────┘  └──────────────────┘
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

**Payload-Validierung (Pydantic v2):**
- `Key`: Pflichtfeld, 1–200 Zeichen, wird getrimmt, darf nicht nur Leerzeichen sein
- `Stufe`: wird normalisiert (z.B. `f3` → `F3`), max. 10 Zeichen
- `Meldung`: max. 5000 Zeichen
- `Nummer`: ≥ 0

**Idempotenz:** Doppelter `Key` → `created: false`, vorhandene `incident_id` wird zurückgegeben.

API-Key erstellen:
```bash
python -m app.cli create-api-key --label "Alarmierungssystem" --org-id 1
```

### Lage-Alarm anlegen

```http
POST /api/v1/lage/alarm
X-API-Key: elh_...
Content-Type: application/json
```

Erstellt eine Einsatzstelle in einer laufenden Großschadenslage. Gleiche Validierungsregeln wie `AlarmPayload`, zusätzlich:
- `Lat`: -90.0 bis +90.0
- `Lng`: -180.0 bis +180.0

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

Der optionale KI-Assistent nutzt die **Anthropic Claude API** und kann pro Org separat aktiviert werden.

### Aktivierung

In den System-Einstellungen (`/admin/system-einstellungen`, nur `system_admin`):
- `ai_enabled = true`
- `ai_api_key = sk-ant-...`

Oder per Org über `/admin/settings` (BYOK — eigener API-Key je Org möglich).

Default: **deaktiviert** (`AI_ENABLED=false`).

### Funktionen

| Funktion | Beschreibung |
|----------|-------------|
| **✨ Auftragsvorschläge** | 3–5 Erstmaßnahmen als Kanban-Tasks; Einsatzleiter bestätigt oder verwirft |
| **✨ Lage-Hinweise** | Taktische Ticker-Hinweise für das Board |
| **✨ Lagebild** | Kompakte Lagebeschreibung aus Live-Einsatzdaten |
| **✨ Einsatzbericht** | KI-Entwurf für den Abschlussbericht |
| **✨ Auto-Priorisierung (Großschadenslage)** | Priorität + `danger_score` + `urgency_score` für Einsatzstellen |

---

## Multi-Organisations-Architektur

Mehrere Feuerwehren können auf einer Instanz betrieben werden und gemeinsam an Einsätzen arbeiten.

```
System-Admin (organisationsübergreifend)
    │
    ├── Organisation A (z. B. FF Wolfurt) — Org-Admin A
    │   ├── Benutzer, Mitglieder, Fahrzeuge (org-isoliert via TenantScoped)
    │   ├── Einstellungen (Logo, Farbe, Zeitzone, KI-Key, Autoclose)
    │   └── Seed-Profile für schnelles Onboarding
    │
    └── Organisation B (z. B. FF Lauterach) — Org-Admin B
        └── ...

Gemeinsamer Einsatz:
    Org A erstellt Einsatz → lädt Org B ein (IncidentOrg)
    → visible_incidents_q() filtert korrekt für alle Beteiligten
    → Fahrzeuge + Mitglieder beider Orgs verfügbar
```

### Row-Level-Isolation

Alle `TenantScoped`-Modelle (AlarmType, Member, TaskSuggestion, MessageSuggestion, LageHint, DefaultMessage, AIPromptVersion) werden automatisch per SQLAlchemy `do_orm_execute`-Event auf die aktuelle Org gefiltert. Der Context wird via `set_tenant_context(db, org_id)` gesetzt.

---

## In-App ZIP-Update

Updates können über die Weboberfläche eingespielt werden — kein SSH erforderlich.

**Ablauf** (`/admin/system/update`, nur `system_admin`):
1. Release-ZIP hochladen
2. Optional: SHA-256-Prüfsumme eingeben
3. System validiert ZIP (Zip-Slip-Schutz), extrahiert, kopiert Dateien
4. Führt `alembic upgrade head` aus
5. Sendet SIGHUP an Gunicorn (graceful reload)

**Release-ZIP erstellen:**
```bash
git archive --format=zip --prefix=release-2.5.0/ HEAD > release-2.5.0.zip
sha256sum release-2.5.0.zip
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
│   ├── permissions.py   require_role(), has_role(), can_access_incident()
│   ├── queries.py       visible_incidents_q() — Tenant-bewusste Einsatz-Abfrage
│   ├── rate_limit.py    slowapi-Instanz + get_api_key_identifier()
│   ├── templating.py    Jinja2-Environment + Zeitzonen-Filter
│   └── audit.py         Audit-Log-Helfer
├── middleware/
│   ├── security_headers.py  CSP, X-Frame-Options, ...
│   └── csrf.py              Double-Submit CSRF-Schutz
├── models/
│   ├── incident.py      Incident, Task, TaskMedia, Message, IncidentOrg, IncidentToken, ...
│   ├── major_incident.py Großschadenslage: IncidentSite, Sector, SiteLogEntry,
│   │                    LageEinheit, LageDispatch, CrossSiteMarker, SiteMedia, ...
│   ├── lagekarte.py     Lagekarte-Geometrie, Marker, Fahrzeug-Positionen
│   ├── user.py          User, Role, ApiKey, AuditLog, DeviceToken, ...
│   ├── master.py        FireDept, VehicleMaster, Member (TenantScoped), OrgSettings,
│   │                    AlarmType (TenantScoped), SeedTemplate, ...
│   ├── invitation.py    OrgInvitation
│   ├── breathing.py     BreathingTroop, TroopMember, PressureLog
│   └── password_reset.py
├── routers/
│   ├── ui_incident.py        Board, Aufgaben, Fahrzeuge, Media-Upload
│   ├── ui_major_incident.py  Großschadenslage, Einsatzkarte, Disposition
│   ├── ui_gsl_staff.py       Stab, SKKM-Einsatzjournal, Funkjournal
│   ├── lagekarte_api.py      Lagekarte, Abschnitte, Marker, Druck
│   ├── ui_weather.py         Wetter-Panel, globale /wetter-Seite
│   ├── ui_media.py           Galerie (/medien), geschützte Datei-Auslieferung
│   ├── ui_breathing.py       Atemschutzüberwachung
│   ├── ui_archive.py         Archiv, PDF-Export
│   ├── ui_admin.py           Stammdaten, Benutzer, API-Keys, Audit
│   ├── ui_settings.py        Org-Einstellungen, ZIP-Update, System-Admin
│   ├── ui_backup.py          Konfig-Export/Import (JSON, Dry-Run)
│   ├── ui_sysadmin.py        System-Admin-Konsole (/admin/system/orgs)
│   ├── ui_invitation.py      Einladungslinks für neue Org-Admins
│   ├── ui_ai_prompts.py      KI-Prompt-Verwaltung
│   ├── ui_profile.py         Benutzer-Profil (Name/E-Mail/Passwort/Avatar)
│   ├── ui_stats.py           Statistik-Dashboard
│   ├── ui_push.py            Web-Push-Verwaltung
│   ├── ui_password_reset.py
│   ├── public.py             Öffentliches Bürger-Meldeportal (+ SMS-Verifikation)
│   ├── api_v1.py             REST-API (Alarmierung, Lage-Alarm)
│   ├── device_api.py         SMS-Gateway-/Geräte-WebSocket-Anbindung
│   ├── ws.py                 WebSocket Pub/Sub
│   └── auth.py               Login / Logout / QR-Login / Geräte-Login
├── services/
│   ├── incident_service.py      Einsatz-Logik, Spalten, Tasks
│   ├── major_incident_service.py Großschadenslage: Stellen, Phasen, Cross-Marker
│   ├── resource_service.py      GSL-Ressourcen + Mehrfach-Disposition
│   ├── lagekarte.py             Lagekarte-Geometrie-Persistenz
│   ├── lagemeldung_service.py   SKKM-Regelkreis: Timer-Logik
│   ├── gsl_lagemeldung_reminder.py Auto-Auftrag bei Überfälligkeit (Loop)
│   ├── gsl_staff_service.py     Stab, Einsatzjournal, Funkjournal
│   ├── site_pages.py            Einsatzstellen-Druck/Seiten
│   ├── weather_service.py       Wetter-Aggregation + Cache + Fallback
│   ├── kachelmann_service.py    Kachelmann Plus-API-Client
│   ├── weather_focus.py         Sturm-/Waldbrand-Szenario-Analyse
│   ├── geocoding.py / geo_service.py  Adresse ↔ Koordinaten
│   ├── address_autocomplete.py  Adress-Suche (Bürgerportal, Pin)
│   ├── media_service.py         Upload-Pipeline (Bild/PDF/Video/HEIC)
│   ├── lage_media_service.py    GSL-Medien (Einsatzstellen-Fotos)
│   ├── storage_service.py       Speicher-Quota-Verwaltung
│   ├── pdf_service.py           WeasyPrint PDF-Generierung
│   ├── push_service.py          Web-Push (VAPID)
│   ├── broadcast.py             WS-Pub/Sub-Manager
│   ├── autoclose.py             Auto-Schließen Hintergrund-Service
│   ├── task_reminder.py         Auftrags-/Meldungs-Fälligkeits-Reminder
│   ├── breathing_service.py     Atemschutz-Logik
│   ├── ai_service.py            Anthropic Claude Integration
│   ├── alarm_service.py         Alarmtyp-Lookup + org-aware
│   ├── seed_service.py          Seed-Template-Anwendung bei Org-Anlage
│   ├── sms_service.py           SMS-Versand via Gateway-Container
│   ├── mail_service.py          SMTP (Passwort-Reset, Einladungen)
│   └── update_service.py        ZIP-Update + Alembic-Migration
├── static/
│   ├── css/app.css          Fertiger Tailwind-Build (committet)
│   ├── js/                  alpine, htmx, sortable, leaflet (+geoman, markercluster),
│   │                        quill, app.js, ...
│   └── img/                 Logo, Favicon, Icons, Leaflet-Marker, taktische Symbole
└── templates/
    ├── base.html            Master-Layout (Nav, Modal, Toasts, WS-Alert)
    ├── incident/            Board-Komponenten, Task/Fahrzeug-Modals
    ├── incident_major/      Großschadenslage: Board, Einsatzkarte, Lagekarte, Stab
    ├── weather/             Wetter-Panel, /wetter-Seite
    ├── profile/             Benutzer-Profil
    ├── public/              Öffentliches Bürger-Meldeportal
    ├── media/               gallery.html
    ├── admin/               sysadmin_orgs.html, konfig.html, ...
    └── ...
alembic/versions/            Migrationen 0001–0079
docs/
├── MIGRATION_RUNBOOK.md     Vollständiger Migrationsleitfaden
├── multi-tenancy-konzept.md Technisches Konzeptdokument
└── wiki/                    GitHub-Wiki-Quelldateien
tests/                       pytest-Suite (26 Testmodule, 335+ Tests)
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
| **2.5.0** | 2026-06-19 | Großschadenslage-Ressourcenverwaltung (Einheiten anlegen/disponieren, Mehrfach-Disposition, Fremdorganisations-Ressourcen, Ressourcen-Journal); Taktische Lagekarte nach ÖBFV E-27 (ÖNORM-Symbole, Magnetfarben, Legende); Lagekarte-Druck A4/A3 mit Druckvorschau, Fußzeile & Print-Center; SKKM-Lagemeldungs-Regelkreis (Lage→Auftrag→Kontrolle); übergreifende Meldungen mit Status-Workflow, Medien & Druck; Einsatzkarte mit Live-Updates & Foto-Upload; Kachelmann-Wetter-Primärquelle; Bürgermeldungs-Foto-Übertragung; Testsystem-Modus (`TEST_SYSTEM=true`: Badge im Header + Hinweis auf allen Ausdrucken) |
| **2.4.0** | 2026-06-13 | Wetterdaten-Integration: Nowcast (15-min), Ist-Werte, +6/+12/+24h-Vorhersage, Unwetterwarnungen (GeoSphere Austria CC BY 4.0); Sturm- und Waldbrand-Szenario-Alerts; Radar-Layer (RainViewer) auf Lagekarte; Wetter-Panel in GSL-Board und Einzeleinsatz; globale `/wetter`-Seite; org-spezifisches Opt-out |
| **2.3.0** | 2026-06-13 | Großschadenslage-Karte: Abschnitte live ohne Reload, Pin-Modus mit Reverse Geocoding, Geoman-Toolbar auf Deutsch; Stab: BMI SKKM-Einsatzjournal als erstes Tab; Dashboard: Abschnitt-Polygone auf Mini-Karte |
| **2.2.0** | 2026-06-11 | Multi-Tenancy vollständig (12 PRs): Row-Level-Isolation, Org-Onboarding, KI je Org, Speicher-Quotas, Einladungsmodell, Auto-Schließen, Rate-Limiting, API-Härtung, System-Konsole, Migration-Runbook |
| **2.0.0** | 2026-05-23 | Media-Upload + Galerie, System-Admin-Rolle, Zeitzone je Org, ZIP-Update, Python 3.14 |
| **1.0.0** | 2026-05-22 | Initiale Webapp (FastAPI + HTMX, WebSocket, Atemschutz, PWA, QR-Code) |

---

## Lizenz

MIT License — Freiwillige Feuerwehr Wolfurt  
Nutzung für alle österreichischen Feuerwehren ausdrücklich erwünscht.
