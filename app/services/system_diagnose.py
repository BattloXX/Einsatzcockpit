"""Org-lose, rein lesende Systemdiagnose ohne Zugangsdaten oder Nutzdaten.

Zeitpunkte sind naive UTC; Darstellung erfolgt erst beim jeweiligen Aufrufer.
Jede Diagnose liefert status (ok/warn/fail), evidence und checked_at.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
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
from app.services import update_service, ws_bus
from app.services.background_runtime import EXPIRED_WORKER_RETENTION
from app.services.worker_gesundheit import worker_ausfaelle

_PROCESS_STARTED_AT = datetime.now(UTC)
logger = logging.getLogger(__name__)


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
    registered = db.query(WorkerRuntime).filter(WorkerRuntime.expires_at >= now - EXPIRED_WORKER_RETENTION)\
        .execution_options(include_all_tenants=True)\
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
        "warn" if worker_ausfaelle(registered, now) or faulty else "ok")
    last_success = max((values.get("last_success_at") for w in registered
                        if w.heartbeat_at > now - timedelta(days=1) for values in w.loops.values()
                        if values.get("last_success_at")), default=None)
    return {"status": state, "checked_at": now, "evidence": {
        "worker_count": len(workers), "workers": worker_rows,
        "heartbeat_age_s": {w.worker_id: max(0, (now - w.heartbeat_at).total_seconds()) for w in registered},
        "expired_workers": [w.worker_id for w in expired],
        "failed_workers": [w.worker_id for w in worker_ausfaelle(registered, now)], "leaders_by_host": leaders_by_host,
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
        logger.exception("Systemdiagnose: DB-Pruefung fehlgeschlagen")
        evidence["error"] = "database_check_failed"
        return {"status": "fail", "evidence": evidence, "checked_at": now}
    try:
        scripts = ScriptDirectory(str(Path(__file__).resolve().parents[2] / "alembic"))
        evidence["head_revisions"] = sorted(scripts.get_heads())
    except Exception:
        logger.exception("Systemdiagnose: Migrationsstand nicht verfuegbar")
        evidence["error"] = "migration_head_unavailable"
        return {"status": "warn", "evidence": evidence, "checked_at": now}
    state = "ok" if evidence["current_revisions"] == evidence["head_revisions"] else "warn"
    return {"status": state, "evidence": evidence, "checked_at": now}


@lru_cache(maxsize=1)
def _git_commit_time() -> str | None:
    """Commit-Zeit pro Prozess einmal lesen, auch bei gepackten Git-Objekten."""
    from app.admin_mcp.zeit import iso_utc

    # HEAD/Refs allein enthalten keine Zeit; Git liest auch Packfiles und Worktrees.
    code, output, _ = update_service._run_git(["log", "-1", "--format=%cI", "HEAD"], timeout=2)
    return iso_utc(output) if code == 0 else None


def version_info() -> dict:
    """Build-Metadaten aus den Laufzeiteinstellungen."""
    from app.admin_mcp.zeit import iso_utc

    build = iso_utc(settings.BUILD_TIME) if settings.BUILD_TIME.strip().lower() not in ("", "unknown") else None
    source = "BUILD_TIME"
    docker = update_service.is_docker()
    if build is None and not docker and update_service.is_git_checkout():
        build = _git_commit_time()
        if build is not None:
            source = "Git-Commit-Zeit (HEAD)"
    missing = build is None
    if build is None:
        try:
            fallback = datetime.fromtimestamp(Path(__file__).stat().st_mtime, UTC)
            source = "Image-Datei-mtime (kein Buildnachweis)" if docker else "Datei-mtime (kein Buildnachweis)"
        except OSError:
            # Linux-Prozessstart, kein frei erfundener Buildzeitpunkt.
            try:
                # Linux /proc: Feld 22 sind Ticks seit Boot; comm kann Leerzeichen enthalten.
                fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
                age = time.clock_gettime(time.CLOCK_BOOTTIME) - int(fields[19]) / os.sysconf("SC_CLK_TCK")
                fallback = datetime.now(UTC) - timedelta(seconds=age)
                source = "Prozessstart (kein Buildnachweis)"
            except (OSError, ValueError, IndexError, AttributeError):
                fallback = _PROCESS_STARTED_AT
                source = "Modulinitialisierung (kein Buildnachweis)"
        build = iso_utc(fallback)
    recommendation = "Keine Aktion erforderlich."
    if missing:
        recommendation = (
            "BUILD_TIME fehlt: CI-Image aus docker-build.yml mit compose pull beziehen "
            "oder beim lokalen Build --build-arg BUILD_TIME=<UTC-ISO-Zeitpunkt> setzen."
            if docker else
            "BUILD_TIME als UTC-ISO-Zeitpunkt setzen oder einen Git-Checkout mit lesbarer HEAD-Commit-Zeit verwenden."
        )
    return {"status": "warn" if missing or not settings.GIT_SHA or settings.GIT_SHA == "unknown" else "ok",
            "checked_at": presence.utc(), "evidence": {
                "APP_VERSION": settings.APP_VERSION, "GIT_SHA": settings.GIT_SHA, "BUILD_TIME": build,
                "build_time_quelle": source,
                "empfehlung": recommendation,
            }}


def service_summary(db: Session) -> dict:
    """Nur bekannte Dienstschluessel/Zustaende zaehlen, niemals freie Meldungen."""
    from sqlalchemy import func

    from app.models.dienst_monitor import DIENST_LABELS, DienstStatus

    now = presence.utc()
    rows = db.query(DienstStatus.key, DienstStatus.state, func.count(DienstStatus.id)).filter(
        DienstStatus.key.in_(DIENST_LABELS),
        DienstStatus.state.in_(("ok", "down", "unknown", "teilweise", "nicht_konfiguriert")),
    ).execution_options(include_all_tenants=True).group_by(DienstStatus.key, DienstStatus.state).all()
    evidence = {key: {"ok": 0, "down": 0, "unknown": 0, "teilweise": 0, "nicht_konfiguriert": 0}
                for key in DIENST_LABELS}
    for key, state, count in rows:
        evidence[key][state] = count
    status = "unbekannt" if not rows else "fail" if any(r[1] == "down" for r in rows) else (
        "warn" if any(r[1] in ("unknown", "teilweise") for r in rows) else "ok")
    return {"status": status, "checked_at": now,
            "evidence": {"dienste": evidence, "datenbasis": "gespeicherter Dienstmonitor-Zustand"
                         if rows else "nicht verfuegbar"}}


def gateway_summary(db: Session) -> dict:
    """Globale SQL-Zaehler; keine Jobs/Inhalte oder Gateway-Namen laden."""
    from sqlalchemy import func

    from app.models.gateway import Gateway, PrintJob
    from app.models.sms import SmsDispatchJob
    from app.models.user import SmsGatewayToken

    now = presence.utc()

    def count(model, *conditions):
        return db.query(func.count(model.id)).filter(*conditions)\
            .execution_options(include_all_tenants=True).scalar() or 0

    evidence: dict = {}
    warn = False
    for kind in ("linux", "windows_agent"):
        valid = (Gateway.kind == kind, Gateway.device_token_hash.isnot(None))
        online = (Gateway.agent_connection_id.isnot(None), Gateway.agent_presence_expires_at > now,
                  Gateway.agent_connection_token_hash == Gateway.device_token_hash)
        evidence[kind] = {"eingerichtet": count(Gateway, *valid), "online": count(Gateway, *valid,
            *online), "nicht_ueberwacht": count(Gateway, *valid, Gateway.offline_alert_enabled.is_(False))}
        monitored = (*valid, Gateway.offline_alert_enabled.is_(True))
        # Online-Zaehler bleiben vollstaendig; nur ueberwachte Ausfaelle warnen.
        warn |= count(Gateway, *monitored) > count(Gateway, *monitored, *online)
    evidence["sms"] = {"eingerichtet": count(SmsGatewayToken, SmsGatewayToken.revoked_at.is_(None)),
        "online": count(SmsGatewayToken, SmsGatewayToken.revoked_at.is_(None),
            SmsGatewayToken.connection_id.isnot(None), SmsGatewayToken.presence_expires_at > now,
            SmsGatewayToken.connection_token_hash == SmsGatewayToken.token_hash)}
    # W&T besitzt keinen eigenen Presence-Lease: gespeicherte Dienstmonitorwerte.
    serial = service_summary(db)["evidence"]["dienste"]["alarm_seriell"]
    evidence["wt"] = {"dienstmonitor": serial,
                      "datenbasis": "nicht verfuegbar" if not sum(serial.values()) else "Dienstmonitor"}
    # Dieselben offenen Zustaende und Faelligkeiten wie im vorhandenen Monitor.
    from sqlalchemy import and_, or_

    from app.services.worker_monitor import PRINT_OPEN, SMS_OPEN

    for label, model, active, overdue in (
        ("druck_jobs", PrintJob, PRINT_OPEN, or_(PrintJob.lease_expires_at <= now,
            and_(PrintJob.lease_expires_at.is_(None), PrintJob.erstellt_am <= now - timedelta(minutes=5)))),
        ("sms_jobs", SmsDispatchJob, SMS_OPEN, or_(SmsDispatchJob.expires_at <= now,
            and_(SmsDispatchJob.status != "queued", SmsDispatchJob.lease_until <= now),
            and_(SmsDispatchJob.status == "queued", SmsDispatchJob.created_at <= now - timedelta(minutes=5)))),
    ):
        pending = model.status.in_(active)
        evidence[label] = {"offen": count(model, pending), "ueberfaellig": count(model, pending, overdue)}
    warn = warn or evidence["sms"]["eingerichtet"] > evidence["sms"]["online"]
    warn = warn or bool(serial["down"] or serial["teilweise"])
    warn = warn or any(evidence[k]["ueberfaellig"] for k in ("druck_jobs", "sms_jobs"))
    return {"status": "warn" if warn else "ok", "checked_at": now, "evidence": evidence}


def deploy_summary(db: Session) -> dict:
    """Deploy-Abstand ohne Statusverschlechterung bei fehlendem GitHub-Abgleich."""
    from app.services.github_readonly import GitHubReadonly, valid_sha

    evidence: dict = {"deploy_sha": settings.GIT_SHA if valid_sha(settings.GIT_SHA) else None,
                      "main_sha": None, "commits_hinter_main": None, "commits_vor_main": None,
                      "aktuell": None, "hinweis": "GitHub-Abgleich nicht verfuegbar"}
    if evidence["deploy_sha"] is None:
        return evidence
    try:
        github = GitHubReadonly(db=db, persist=False)
        head = github.head_of_main()
        evidence["main_sha"] = (head.get("daten") or {}).get("sha")
        comparison = github.compare(settings.GIT_SHA)
        data = comparison.get("daten") or {}
        behind = data.get("commits_hinter_main")
        if comparison.get("status") == "ok" and type(behind) is int and behind >= 0:
            evidence.update(commits_hinter_main=behind, commits_vor_main=data.get("commits_vor_main"),
                            aktuell=behind == 0, hinweis="Deploy entspricht main oder liegt davor"
                            if behind == 0 else "Deploy liegt hinter main")
    except Exception:
        logger.exception("Systemdiagnose: GitHub-Abgleich fehlgeschlagen")
        evidence["hinweis"] = "GitHub-Abgleich nicht verfuegbar"
    return evidence


def push_summary(db: Session, org_id: int | None = None, limit: int = 20, quelle: str | None = None) -> dict:
    """FCM-Konfiguration und begrenzte Zustellnachweise ohne Nachrichten oder Personen lesen."""
    import importlib.util
    import os

    from sqlalchemy import case, func

    from app.models.user import FcmDeliveryLog, FcmToken, PushLog, User
    from app.services import push_service

    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Ungueltiges Push-Limit")
    now = presence.utc()
    cfg = push_service._push_cfg(db)
    fcm = {"enabled": bool(cfg.get("fcm_enabled")),
           "project_id_gesetzt": bool(cfg.get("fcm_project_id")),
           "credentials_datei_vorhanden": os.path.isfile(cfg.get("fcm_credentials_path") or ""),
           "firebase_admin_installiert": importlib.util.find_spec("firebase_admin") is not None,
           "initialisiert": push_service._fcm_app is not None,
           "nicht_verfuegbar_markiert": push_service._fcm_unavailable}
    # Kommagetrennte Faehigkeiten wie im Versandpfad, keine Teilworttreffer.
    native = func.instr("," + func.coalesce(FcmToken.capabilities, "") + ",", ",native_alarm,") > 0
    token_query = db.query(User.org_id, func.count(FcmToken.id),
        func.sum(case((native, 1), else_=0)),
        func.sum(case((FcmToken.last_used_at >= now - timedelta(days=7), 1), else_=0)),
    ).join(User, User.id == FcmToken.user_id).execution_options(include_all_tenants=True)
    if org_id is not None:
        token_query = token_query.filter(User.org_id == org_id)
    tokens = [{"org_id": org, "anzahl": count, "native_alarm": int(native_count), "aktiv_7_tage": int(active)}
              for org, count, native_count, active in token_query.group_by(User.org_id).order_by(User.org_id).all()]
    configured = all(fcm[key] for key in ("enabled", "project_id_gesetzt", "credentials_datei_vorhanden",
                                         "firebase_admin_installiert"))
    ready = False
    if tokens and configured and not fcm["nicht_verfuegbar_markiert"]:
        try:
            ready = push_service._get_fcm_app(cfg) is not None
        except Exception:
            logger.exception("Systemdiagnose: FCM-Initialisierung fehlgeschlagen")
            ready = False
        fcm["initialisiert"] = push_service._fcm_app is not None
        fcm["nicht_verfuegbar_markiert"] = push_service._fcm_unavailable
    query = db.query(PushLog.id, PushLog.sent_at, PushLog.source, PushLog.org_id,
                     PushLog.sent_count, PushLog.total_count).execution_options(include_all_tenants=True)
    if org_id is not None:
        query = query.filter(PushLog.org_id == org_id)
    if quelle is not None:
        query = query.filter(PushLog.source == quelle)
    pushes = []
    summary = {"gesendet": 0, "angenommen_ohne_ack": 0, "fehlgeschlagen": 0, "bestaetigt": 0}

    def iso(value):
        return presence.utc(value).isoformat() + "Z" if value is not None else None

    for push in query.order_by(PushLog.sent_at.desc(), PushLog.id.desc()).limit(limit).all():
        deliveries = db.query(FcmDeliveryLog.success, FcmDeliveryLog.error_code, FcmToken.id,
            FcmToken.capabilities, FcmDeliveryLog.sent_at, FcmDeliveryLog.delivered_at,
        ).outerjoin(FcmToken, FcmToken.id == FcmDeliveryLog.fcm_token_id).filter(
            FcmDeliveryLog.push_log_id == push.id).execution_options(include_all_tenants=True)
        # Die Zusammenfassung umfasst alle Zustellversuche der ausgewaehlten Pushes.
        summary["angenommen_ohne_ack"] += deliveries.filter(FcmDeliveryLog.success.is_(True),
            FcmDeliveryLog.delivered_at.is_(None), FcmDeliveryLog.sent_at < now - timedelta(seconds=60)).count()
        summary["fehlgeschlagen"] += deliveries.filter(FcmDeliveryLog.success.is_(False)).count()
        summary["bestaetigt"] += deliveries.filter(FcmDeliveryLog.delivered_at.isnot(None)).count()
        gesendet = deliveries.filter(FcmDeliveryLog.success.is_(True)).count()
        bestaetigt = deliveries.filter(FcmDeliveryLog.delivered_at.isnot(None)).count()
        # Alte PushLog-Zaehler enthalten nur Web-Push. FCM immer aus Nachweisen lesen.
        total = db.query(FcmDeliveryLog).filter(
            FcmDeliveryLog.push_log_id == push.id,
            ~((FcmDeliveryLog.error_code == "fcm_not_configured")
              & FcmDeliveryLog.user_id.is_(None) & FcmDeliveryLog.fcm_token_id.is_(None))
            | FcmDeliveryLog.error_code.is_(None),
        ).execution_options(include_all_tenants=True).count()
        summary["gesendet"] += gesendet
        rows = deliveries.order_by(FcmDeliveryLog.sent_at.desc(), FcmDeliveryLog.id.desc()).limit(20).all()
        pushes.append({"id": push.id, "sent_at": iso(push.sent_at), "quelle": push.source,
            "org_id": push.org_id, "sent_count": gesendet, "total_count": total,
            "bestaetigt": bestaetigt,
            "zustellungen": [{"success": row.success, "error_code": row.error_code,
                "native_alarm": "native_alarm" in (row.capabilities or "").split(",") if row.id is not None else None,
                "delivered_at": iso(row.delivered_at),
                "ack_sekunden": (presence.utc(row.delivered_at) - presence.utc(row.sent_at)).total_seconds()
                    if row.delivered_at is not None else None} for row in rows]})
    state = "fail" if tokens and not ready else "warn" if (
        summary["angenommen_ohne_ack"] or summary["fehlgeschlagen"]) else "ok"
    return {"status": state, "checked_at": now,
            "evidence": {"fcm": fcm, "tokens_je_org": tokens, "pushes": pushes, "zusammenfassung": summary}}


def backup_status(now: datetime | None = None) -> dict:
    """Gemeinsamer Teilcheck fuer Sicherung und Restore-Probe."""
    from app.services.backup_status import pruefen
    return pruefen(now=now)
