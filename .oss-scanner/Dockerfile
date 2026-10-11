# Build-Image für den Anthropic OSS Scanner (https://github.com/anthropics/oss-scanner).
# Nicht das Produktions-Image (siehe /Dockerfile). Der Scanner baut mit dem Repo als
# Build-Kontext, danach läuft die Analyse OHNE Netz – alles für Build und Tests wird hier geholt.
FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    # Test-/Analyseumgebung: SQLite, Netz-Loops aus, Dummy-Schlüssel (nie produktiv nutzen)
    DATABASE_URL=sqlite:///./test.db \
    SECRET_KEY=oss-scanner-test-secret-key-mindestens-32-zeichen \
    DEBUG=true \
    NACHSCHLAGEWERK_SYNC_ENABLED=false

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates build-essential git curl \
      libmariadb-dev pkg-config \
      libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi-dev \
      ffmpeg poppler-utils tesseract-ocr tesseract-ocr-deu \
      nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Scanner-Vertrag: der Checkout liegt im Image unter /src
COPY . /src
WORKDIR /src

# Python-Abhängigkeiten inkl. Test-/Lint-Werkzeuge
RUN pip install --no-cache-dir -e ".[dev]"

# Frontend (Tailwind-Build) und JS-Testabhängigkeiten
RUN npm ci && npm run build

# Günstige Checks, damit das Image nachweislich funktioniert; Fehlschläge brechen den Build nicht ab.
RUN (python -m ruff check app/ || echo "WARNING: ruff meldet Befunde") \
    && (npm run test:js || echo "WARNING: JS-Tests fehlgeschlagen") \
    && (python -m pytest -q -x tests/test_public_tenant_isolation.py tests/test_startup_secrets.py \
        || echo "WARNING: pytest-Smoke fehlgeschlagen") \
    && rm -f test.db
