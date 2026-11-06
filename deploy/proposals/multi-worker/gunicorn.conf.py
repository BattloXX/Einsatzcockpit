"""Opt-in-Vorschlag; bestehende Service-/Docker-Kommandos bleiben unveraendert."""

import faulthandler
import os
import sys
from contextlib import suppress

raw_workers = os.environ.get("ECP_GUNICORN_WORKERS", "1")
if raw_workers not in {"1", "2", "3", "4"}:
    raise RuntimeError("ECP_GUNICORN_WORKERS muss 1, 2, 3 oder 4 sein")
workers = int(raw_workers)
if workers > 1 and not os.environ.get("REDIS_URL", "").strip():
    raise RuntimeError("Mehrere Worker benoetigen einen konfigurierten Redis-Bus")

pidfile = os.environ.get("GUNICORN_PID_FILE") or None

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
if os.path.isdir("/dev/shm"):
    worker_tmp_dir = "/dev/shm"


def worker_abort(worker):
    """Bei SIGABRT alle Thread-Stacks sichern, auch wenn das Logging scheitert."""
    with suppress(Exception):
        worker.log.critical("WORKER TIMEOUT: Thread-Stacks fuer PID %s", worker.pid)
    with suppress(Exception):
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)

# Empfehlung DB_POOL_SIZE/DB_MAX_OVERFLOW je Workerzahl:
# 2 Worker: 10/20, 3 Worker: 8/15, 4 Worker: 8/12.
def on_starting(server):
    pool_size = int(os.environ.get("DB_POOL_SIZE", "10"))
    max_overflow = int(os.environ.get("DB_MAX_OVERFLOW", "30"))
    max_connections = int(os.environ.get("DB_MAX_CONNECTIONS", "151"))
    budget = workers * (pool_size + max_overflow) + 20
    if budget > max_connections:
        server.log.warning(
            "DB-Verbindungsbudget UEBERSCHRITTEN: %s Worker * (%s Pool + %s Overflow) "
            "+ 20 Reserve = %s > DB_MAX_CONNECTIONS=%s; Pools reduzieren oder MariaDB-Limit pruefen",
            workers, pool_size, max_overflow, budget, max_connections,
        )
