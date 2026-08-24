# Lokale Entwicklung

← [Zurück zur Startseite](Home)

## Voraussetzungen

- Python 3.14
- Docker (für lokale MariaDB) oder eine lokale MariaDB-Installation
- Node.js 20+ (für den Tailwind-CSS-Build)
- Git
- Optional: `ffmpeg` für Video-Uploads

## Repository klonen

```bash
git clone https://github.com/BattloXX/Einsatzcockpit.git
cd Einsatzcockpit
```

## Virtuelle Umgebung

```bash
python3.14 -m venv .venv
source .venv/bin/activate      # Linux/Mac
# oder:
.venv\Scripts\activate         # Windows
```

## Abhängigkeiten installieren

```bash
pip install -e ".[dev]"
npm install
npm run build
```

Für laufendes CSS-Kompilieren in einem zweiten Terminal:

```bash
npm run dev
```

## MariaDB via Docker

```bash
docker run -d \
  --name fwwo-db \
  -e MARIADB_ROOT_PASSWORD=root \
  -e MARIADB_DATABASE=einsatzleiter \
  -e MARIADB_USER=einsatzleiter \
  -e MARIADB_PASSWORD=devpassword \
  -p 3306:3306 \
  mariadb:10.11
```

## `.env` anlegen

```bash
cp .env.example .env
```

`.env` für lokale Entwicklung:

```ini
DATABASE_URL=mysql+pymysql://einsatzleiter:devpassword@127.0.0.1:3306/einsatzleiter
SECRET_KEY=local-dev-secret-not-for-production
APP_BASE_URL=http://localhost:8092
BOOTSTRAP_ADMIN_USER=admin
BOOTSTRAP_ADMIN_PASSWORD=admin123
# VAPID-Keys optional für lokale Entwicklung:
VAPID_PRIVATE_KEY=
VAPID_PUBLIC_KEY=
# Rate-Limits (optional, Defaults passen für Entwicklung):
# LOGIN_RATELIMIT=10/minute
# API_ALARM_RATELIMIT=60/minute
# UPLOAD_RATELIMIT=20/minute
```

## Datenbank initialisieren

```bash
# Warten bis MariaDB bereit ist:
sleep 5

alembic upgrade head
python -m app.seed_data
```

## Entwicklungsserver starten

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8092
```

Browser: `http://localhost:8092`
Login: `admin` / `admin123`

`--reload` startet den Server bei jeder Code-Änderung neu.

## Typische Entwicklungsaufgaben

### Neue Alembic-Migration erstellen

```bash
alembic revision --autogenerate -m "beschreibung der aenderung"
alembic upgrade head
```

### Seed-Daten neu laden

```bash
python -m app.seed_data
```

### Tests ausführen

```bash
pytest tests/ -v
```

### Linting

```bash
ruff check app/
ruff check --fix app/   # Auto-Fix
```

### Type-Check

```bash
mypy app/ --ignore-missing-imports
```

## Hot-Reload für Templates

Jinja2-Templates werden bei `--reload` automatisch neu geladen. CSS/JS-Änderungen: Browser-Cache leeren (Strg+Shift+R).

## WebSocket lokal testen

Zwei Browser-Fenster mit demselben Einsatz öffnen → Änderungen sollten in Echtzeit synchronisiert werden.

## WeasyPrint lokal (macOS)

```bash
brew install pango
pip install weasyprint
```

## WeasyPrint lokal (Windows)

WeasyPrint unter Windows benötigt GTK-Runtime. Empfohlen: WSL2 oder Docker für Windows-Entwicklung.
