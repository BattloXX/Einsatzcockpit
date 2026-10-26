"""Opt-in-Vorschlag; bestehende Service-/Docker-Kommandos bleiben unveraendert."""

import os

raw_workers = os.environ.get("ECP_GUNICORN_WORKERS", "1")
if raw_workers not in {"1", "2", "3", "4"}:
    raise RuntimeError("ECP_GUNICORN_WORKERS muss 1, 2, 3 oder 4 sein")
workers = int(raw_workers)
if workers > 1 and not os.environ.get("REDIS_URL", "").strip():
    raise RuntimeError("Mehrere Worker benoetigen einen konfigurierten Redis-Bus")

worker_class = "app.uvicorn_worker.TolerantUvicornWorker"
bind = os.environ.get("ECP_GUNICORN_BIND", "127.0.0.1:8092")
timeout = 120
graceful_timeout = 120
keepalive = 5
accesslog = "-"
# Query-Strings und Referer koennen signierte Artefakt- oder Monitoring-Tokens enthalten.
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(D)s'
errorlog = "-"
preload_app = False
