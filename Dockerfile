FROM node:20-alpine AS frontend

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY tailwind.config.js ./
COPY app/static/css/tailwind.input.css app/static/css/tailwind.input.css
COPY app/static/js app/static/js
COPY app/templates app/templates
RUN npm run build

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmariadb-dev \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf2.0-0 \
        libffi-dev \
        build-essential \
        ffmpeg \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-deu \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
COPY --from=frontend /build/app/static/css/app.css app/static/css/app.css
RUN pip install --no-cache-dir -e . \
    && addgroup --system einsatzcockpit \
    && adduser --system --ingroup einsatzcockpit --home /app einsatzcockpit \
    && mkdir -p /app/app_storage \
    && chown -R einsatzcockpit:einsatzcockpit /app

COPY --chown=einsatzcockpit:einsatzcockpit deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

USER einsatzcockpit
EXPOSE 8092

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "--bind", "0.0.0.0:8092", "--timeout", "120", "--keep-alive", "5", "--access-logfile", "-", "--error-logfile", "-"]
