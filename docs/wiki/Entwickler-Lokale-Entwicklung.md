# Lokale Entwicklung

← [Zurück zur Startseite](Home)

## Voraussetzungen

- Python 3.14
- Docker (für lokale MariaDB) oder eine lokale MariaDB-Installation
- Node.js 22+ (für den Tailwind-CSS-Build und Bibliotheks-Updates)
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

## Frontend-Bibliotheken aktualisieren

Third-Party-Bibliotheken sind mit exakten Versionen in `package.json` erfasst.
`scripts/vendor-js.mjs` kopiert die zugeordneten JS- und CSS-Dateien aus den npm-Paketen:

```bash
npm ci
# Gewünschte Version in package.json anpassen, danach Lockdatei aktualisieren:
npm install
npm run vendor
npm run vendor:check
```

`package.json`, `package-lock.json` und die kopierten Dateien gemeinsam committen.
Third-Party-Dateien unter `app/static/js` nie von Hand ersetzen.
Die separat minifizierten `leaflet.min.js` und `leaflet.min.css` sind nicht im
Mapping, da sie vom npm-Paket abweichen.

Dependabot aktualisiert npm-, pip- und Actions-Abhängigkeiten über
`.github/dependabot.yml`. Bei Vendor-PRs nach dem Checkout `npm ci && npm run vendor`
ausführen und die kopierten Dateien mitcommitten; der CI-Job JS prüft sie mit
`npm run vendor:check`. Major-Updates von quill-cursors sind ausgenommen:
Version 5.0.0 bleibt, weil 6.x nur ESM bietet und die bisherige Script-Einbindung bricht.
Bei Konva bleibt `Konva.legacyTextRendering = true` in `app/static/js/annotate.js`
für die bisherige Textdarstellung gesetzt.

Die vendorten Dateien sind im Repository committet. Der Server braucht weiterhin
kein Node.js; `npm run vendor` ist nur lokal bei Bibliotheks-Updates nötig.

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
