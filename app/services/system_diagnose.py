"""Org-lose, rein lesende Systemdiagnose ohne Zugangsdaten oder Nutzdaten.

Zeitpunkte sind naive UTC; Darstellung erfolgt erst beim jeweiligen Aufrufer.
Jede Diagnose liefert status (ok/warn/fail), evidence und checked_at.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.worker_runtime import WorkerRuntime
from app.services import gateway_presence as presence
from app.services import ws_bus


def _datetime(value):
    return presence.utc(datetime.fromisoformat(value)) if value else None


def active_workers(db: Session, now: datetime | None = None) -> list[WorkerRuntime]:
    """Aktive Worker ohne Org-Kontext laden."""
    now = presence.utc(now)
    return db.query(WorkerRuntime).filter(WorkerRuntime.expires_at > now)\
        .execution_options(include_all_tenants=True).order_by(WorkerRuntime.worker_id).all()


def worker_status(db: Session, now: datetime | None = None) -> dict:
    """Heartbeats und Leader je Host; abgelaufene Worker sind keine Leader."""
    now = presence.utc(now)
    registered = db.query(WorkerRuntime).execution_options(include_all_tenants=True)\
        .order_by(WorkerRuntime.worker_id).all()
    workers = [w for w in registered if w.expires_at > now]
    expired = [w for w in registered if w.expires_at <= now]
    leaders = [w for w in workers if w.is_leader]
    worker_rows: list[dict] = []
    for worker in workers:
        loops = []
        for name, values in sorted(worker.loops.items()):
            row = {"name": name, **values}
            last_start = values.get("last_started_at")
            running_age = 0.0
            if values.get("state") == "running" and last_start:
                try:
                    running_age = max(0, (now - presence.utc(datetime.fromisoformat(last_start))).total_seconds())
                except ValueError:
                    running_age = float("inf")
            row["overdue"] = running_age > max(60, 3 * values.get("interval_s", 0))
            for field in ("last_started_at", "last_success_at", "last_failure_at"):
                if row.get(field):
                    row[field] = _datetime(row[field])
            loops.append(row)
        worker_rows.append({"worker_id": worker.worker_id, "hostname": worker.hostname, "pid": worker.pid,
                            "heartbeat_at": worker.heartbeat_at, "expires_at": worker.expires_at,
                            "leader": worker.is_leader, "leader_since": worker.leader_since,
                            "leader_error": worker.leader_error,
                            "redis": {**worker.redis, "last_error_at": _datetime(worker.redis.get("last_error_at"))},
                            "loops": loops})
    redis_workers = [w for w in workers if w.redis.get("configured")]
    redis_up = sum(bool(w.redis.get("connected")) for w in redis_workers)
    configured = redis_status()["evidence"]["configured"] or bool(redis_workers)
    redis_state = ("not_configured" if not configured else "down" if not redis_workers
                   else "ok" if redis_up == len(redis_workers)
                   else "down" if not redis_up else "partial")
    leaders_by_host: dict[str, list[str]] = {}
    for worker in leaders:
        leaders_by_host.setdefault(worker.hostname, []).append(worker.worker_id)
    faulty = any(w["leader_error"] or any(loop["overdue"] or loop["state"] == "failed"
                 for loop in w["loops"]) for w in worker_rows)
    state = "fail" if not leaders or any(len(ids) > 1 for ids in leaders_by_host.values()) else (
        "warn" if expired or faulty else "ok")
    last_success = max((values.get("last_success_at") for w in registered
                        if w.heartbeat_at > now - timedelta(days=1) for values in w.loops.values()
                        if values.get("last_success_at")), default=None)
    return {"status": state, "checked_at": now, "evidence": {
        "worker_count": len(workers), "workers": worker_rows,
        "heartbeat_age_s": {w.worker_id: max(0, (now - w.heartbeat_at).total_seconds()) for w in registered},
        "expired_workers": [w.worker_id for w in expired], "leaders_by_host": leaders_by_host,
        "leader_workers": [w.worker_id for w in leaders], "last_success_at": _datetime(last_success),
        "redis": {"state": redis_state, "configured": configured,
                  "connected_workers": redis_up, "configured_workers": len(redis_workers)},
    }}


def redis_status() -> dict:
    """Lokaler Redis-Status ohne Netzwerkzugriff; nicht konfiguriert ist warn."""
    data = ws_bus.status()
    state = "warn" if not data["configured"] else "ok" if data["connected"] else "fail"
    return {"status": state, "evidence": data, "checked_at": presence.utc()}


def db_status(db: Session) -> dict:
    """Verbindung, verfuegbare Pool-Kennzahlen und Alembic-Stand rein lesend pruefen."""
    now = presence.utc()
    evidence: dict = {"connected": False, "pool": {}, "current_revisions": [], "head_revisions": []}
    try:
        bind = db.get_bind()
        engine = bind.engine if isinstance(bind, Connection) else bind
        pool = engine.pool
        for name in ("size", "checkedin", "checkedout", "overflow"):
            metric = getattr(pool, name, None)
            if callable(metric):
                evidence["pool"][name] = metric()
        db.execute(text("SELECT 1"))
        evidence["connected"] = True
        evidence["current_revisions"] = sorted(MigrationContext.configure(db.connection()).get_current_heads())
    except SQLAlchemyError:
        evidence["error"] = "database_check_failed"
        return {"status": "fail", "evidence": evidence, "checked_at": now}
    try:
        scripts = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "alembic"))
        evidence["head_revisions"] = sorted(scripts.get_heads())
    except Exception:
        evidence["error"] = "migration_head_unavailable"
        return {"status": "warn", "evidence": evidence, "checked_at": now}
    state = "ok" if evidence["current_revisions"] == evidence["head_revisions"] else "warn"
    return {"status": state, "evidence": evidence, "checked_at": now}


def version_info() -> dict:
    """Build-Metadaten aus den Laufzeiteinstellungen."""
    return {"status": "ok" if settings.GIT_SHA and settings.GIT_SHA != "unknown" else "warn",
            "checked_at": presence.utc(), "evidence": {
                "APP_VERSION": settings.APP_VERSION, "GIT_SHA": settings.GIT_SHA, "BUILD_TIME": settings.BUILD_TIME,
            }}
