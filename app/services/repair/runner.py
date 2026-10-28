"""Fail-closed Reparaturablauf mit DB-Mutex, Simulation und Live-Freigaben."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, aliased

from app.admin_mcp.context import allowed
from app.core.audit import write_audit
from app.core.security import hash_api_key
from app.models.admin_repair import AdminApproval, AdminRepairRun
from app.models.admin_security import utcnow
from app.models.master import SystemSettings
from app.models.user import AuditLog, User
from app.services import totp_service
from app.services.db_transactions import ist_wiederholbar
from app.services.diagnose.runner import einsatz_aktiv
from app.services.repair.registry import ACTIONS, Action, Ergebnis, RepairContext, digest, simulate


def prepare_session(db: Session) -> None:
    """Nur Reparaturrequests: Live-Prüfungen dürfen keinen alten MVCC-Snapshot lesen."""
    bind = db.get_bind()
    if bind.dialect.name in ("mysql", "mariadb"):
        if db.in_transaction():
            raise ValueError("Reparatursession muss vor der ersten Abfrage vorbereitet werden")
        # OptionEngine verändert weder den globalen Pool noch andere Requests.
        # Auch nach einem Commit verwenden weitere Checkouts READ COMMITTED.
        db.bind = bind.execution_options(isolation_level="READ COMMITTED")


def switch(db: Session, key: str) -> str:
    row = db.scalar(select(SystemSettings).where(SystemSettings.key == key).execution_options(populate_existing=True))
    return row.value or "" if row else ""


def gates(db: Session) -> None:
    if not totp_service.enabled(db):
        raise ValueError("2FA erforderlich")
    if switch(db, "admin_repair_enabled") != "true":
        raise ValueError("Reparaturen deaktiviert")


def live_user(db: Session, user_id: int, epoch: int, *, web: bool = False) -> User:
    db.expire_all()  # Rollen und Schalter nicht aus einem früheren Request übernehmen.
    user = db.query(User).execution_options(include_all_tenants=True).filter(User.id == user_id).first()
    permitted = bool(user and user.active and user.is_system_admin and not user.is_device) if web else allowed(db, user)
    if not permitted or user is None or user.session_epoch != epoch:
        raise ValueError("Berechtigung widerrufen")
    gates(db)
    return user


def audit(
    db: Session, stage: str, user_id: int, correlation: str, run_id: int | None = None, reason: str | None = None
) -> None:
    write_audit(
        db,
        "admin_repair." + stage,
        user_id=user_id,
        entity_type="admin_repair_run",
        entity_id=run_id,
        payload={"korrelations_id": correlation, "grund": reason},
    )


def action_hash(run: AdminRepairRun) -> str:
    return digest([run.aktion_id, run.scope, run.simulation_hash])


def requirement(db: Session, action: Action) -> str:
    if action.risiko == "low" and action.id in switch(db, "admin_repair_low_auto").split(","):
        return "automatisch"
    return {"low": "bestaetigung", "medium": "bestaetigung", "high": "web_2fa", "critical": "nur_simulation"}[
        action.risiko
    ]


def get_run(db: Session, run_id: int, user_id: int) -> AdminRepairRun:
    run = db.get(AdminRepairRun, run_id, populate_existing=True)
    if run is None or run.angefordert_von != user_id:
        raise ValueError("Lauf nicht gefunden")
    return run


def check_simulation(run: AdminRepairRun, simulation_hash: str, *, laufend: bool = False) -> None:
    if (
        run.status not in (("laufend",) if laufend else ("simuliert", "freigegeben"))
        or not run.simulated_at
        or not timedelta(0) <= utcnow() - run.simulated_at <= timedelta(minutes=15)
        or not run.simulation_hash
        or not secrets.compare_digest(run.simulation_hash, simulation_hash)
    ):
        raise ValueError("Simulation ungueltig oder veraltet; neu simulieren")


def issue_approval(db: Session, run: AdminRepairRun, user_id: int, correlation: str) -> str:
    """Nur die Webroute ruft dies nach erfolgreicher neuer TOTP-Prüfung auf."""
    check_simulation(run, run.simulation_hash or "")
    if run.risiko != "high" or run.angefordert_von != user_id:
        raise ValueError("Keine HIGH-Freigabe moeglich")
    now = utcnow()
    token = secrets.token_urlsafe(32)
    db.add(
        AdminApproval(
            repair_run_id=run.id,
            user_id=user_id,
            token_hash=hash_api_key(token),
            aktion_hash=action_hash(run),
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
    )
    run.status = "freigegeben"
    audit(db, "freigegeben", user_id, correlation, run.id)
    db.flush()
    return token


def consume_approval(db: Session, run: AdminRepairRun, user_id: int, token: str | None) -> None:
    try:
        _consume_approval(db, run, user_id, token)
    except OperationalError as exc:
        # MariaDB 12.3 kann konkurrierende Änderungen bereits beim Locking-Read
        # mit 1020 ablehnen. Keine Wiederholung mit möglicherweise verbrauchtem Token.
        if ist_wiederholbar(exc):
            raise ValueError("Web-Freigabe gleichzeitig verwendet; Status pruefen") from None
        raise


def _consume_approval(db: Session, run: AdminRepairRun, user_id: int, token: str | None) -> None:
    if not token or len(token) > 128:
        raise ValueError("Web-Freigabe erforderlich")
    approval = db.scalar(
        select(AdminApproval)
        .where(
            AdminApproval.token_hash == hash_api_key(token),
            AdminApproval.repair_run_id == run.id,
            AdminApproval.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = utcnow()
    if approval is None or not timedelta(0) < approval.expires_at - approval.created_at <= timedelta(minutes=10):
        raise ValueError("Web-Freigabe ungueltig, abgelaufen oder verbraucht")
    result = db.execute(
        update(AdminApproval)
        .where(
            AdminApproval.repair_run_id == run.id,
            AdminApproval.user_id == user_id,
            AdminApproval.token_hash == hash_api_key(token),
            AdminApproval.aktion_hash == action_hash(run),
            AdminApproval.used_at.is_(None),
            AdminApproval.created_at <= now,
            AdminApproval.expires_at > now,
        )
        .values(used_at=now)
    )
    if getattr(result, "rowcount", 0) != 1:
        raise ValueError("Web-Freigabe ungueltig, abgelaufen oder verbraucht")


def claim_run(
    db: Session, run_id: int, key: str, expected_hash: str | None = None, requested_at: datetime | None = None
) -> bool:
    """Bedingter UPDATE + UNIQUE-Mutex gelten über alle Hosts und Worker hinweg."""
    run = db.get(AdminRepairRun, run_id)
    if run is None:
        return False
    other = aliased(AdminRepairRun)
    admission = requested_at or utcnow()
    conflict = (
        select(other.id)
        .where(
            other.aktion_id == run.aktion_id,
            other.id != run_id,
            or_(other.active_aktion == run.aktion_id, other.started_at >= admission),
        )
        .exists()
    )
    try:
        result = db.execute(
            update(AdminRepairRun)
            .where(
                AdminRepairRun.id == run_id,
                AdminRepairRun.status.in_(("simuliert", "freigegeben")),
                AdminRepairRun.idempotency_key.is_(None),
                AdminRepairRun.simulation_hash == (expected_hash or run.simulation_hash),
                ~conflict,
            )
            .values(status="laufend", active_aktion=run.aktion_id, idempotency_key=key, started_at=utcnow())
        )
        if getattr(result, "rowcount", 0) != 1:
            db.rollback()
            return False
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    except OperationalError as exc:
        db.rollback()
        if ist_wiederholbar(exc) or (
            exc.orig is not None and exc.orig.args and exc.orig.args[0] == "database is locked"
        ):
            return False
        raise


def public_run(run: AdminRepairRun) -> dict:
    return {
        "run_id": run.id,
        "aktion_id": run.aktion_id,
        "risiko": run.risiko,
        "status": run.status,
        "simulation_hash": run.simulation_hash,
        "vorschau": run.vorschau,
        "ergebnis": run.ergebnis,
        "verifikation": run.verifikation,
        "korrelations_id": run.korrelations_id,
    }


def execute(
    db: Session,
    run_id: int,
    user_id: int,
    epoch: int,
    correlation: str,
    simulation_hash: str,
    idempotency_key: str,
    bestaetigt: bool = False,
    approval_token: str | None = None,
    authorize: Callable[[], object] | None = None,
    *, web: bool = False,
) -> dict:
    requested_at = utcnow()
    if (db.get_bind().dialect.name in ("mysql", "mariadb")
            and db.connection().get_isolation_level() != "READ COMMITTED"):
        raise ValueError("Aktuelle Transaktionssicht fuer Reparaturen erforderlich")
    live_user(db, user_id, epoch, web=web)
    if authorize:
        authorize()
    run = get_run(db, run_id, user_id)
    if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
        raise ValueError("Idempotency-Key mit 8 bis 128 Zeichen erforderlich")
    key = hash_api_key(idempotency_key)
    previous = db.scalar(select(AdminRepairRun).where(AdminRepairRun.idempotency_key == key))
    if previous is not None:
        if previous.aktion_id != run.aktion_id or previous.scope != run.scope or previous.angefordert_von != user_id:
            raise ValueError("Idempotency-Key bereits fuer anderen Scope verwendet")
        return public_run(previous)
    action = ACTIONS[run.aktion_id]
    if action.risiko == "critical":
        raise ValueError("CRITICAL ueber MCP gesperrt")
    check_simulation(run, simulation_hash)
    if not action.einsatzsicher and einsatz_aktiv(db):
        raise ValueError("Wegen laufendem Einsatz gesperrt")
    need = requirement(db, action)
    if need != "automatisch" and bestaetigt is not True:
        raise ValueError("Ausdrueckliche Bestaetigung erforderlich")
    scope = action.scope_schema.model_validate(run.scope)
    current = simulate(db, action, scope)
    if digest([action.id, run.scope, current.public()]) != simulation_hash:
        raise ValueError("Datenbestand geaendert; neu simulieren")
    if need == "web_2fa":
        consume_approval(db, run, user_id, approval_token)
    audit(db, "freigegeben", user_id, correlation, run.id)
    if not claim_run(db, run.id, key, simulation_hash, requested_at):
        raise ValueError("Aktion bereits laufend oder Idempotency-Key belegt")
    ident = run.id
    try:
        # SQLite erhält vor dem zweiten Snapshot einen Schreiblock; MariaDB
        # sperrt zusätzlich in jeder Aktion alle ausgewählten Zeilen.
        db.execute(
            update(AdminRepairRun)
            .where(
                AdminRepairRun.id == ident,
                AdminRepairRun.status == "laufend",
                AdminRepairRun.active_aktion == action.id,
            )
            .values(status="laufend")
        )
        live_user(db, user_id, epoch, web=web)
        if authorize:
            authorize()
        if requirement(db, action) != "automatisch" and bestaetigt is not True:
            raise ValueError("Ausdrueckliche Bestaetigung erforderlich")
        if not action.einsatzsicher and einsatz_aktiv(db):
            raise ValueError("Wegen laufendem Einsatz gesperrt")
        run = get_run(db, ident, user_id)
        if digest([action.id, run.scope, simulate(db, action, scope).public()]) != simulation_hash:
            raise ValueError("Datenbestand geaendert; neu simulieren")

        check_simulation(run, simulation_hash, laufend=True)

        def progress(result: Ergebnis):
            row = db.get(AdminRepairRun, ident)
            assert row is not None
            row.ergebnis = result.public()
            audit(db, "teilausfuehrung", user_id, correlation, ident)
            db.commit()

        result = action.impl.ausfuehren(
            db, scope, RepairContext(ident, user_id, correlation, current.daten_hash, progress)
        )
        run = get_run(db, ident, user_id)
        run.ergebnis = result.public()
        run.status, run.active_aktion, run.finished_at = "erfolgreich", None, utcnow()
        audit(db, "ausgefuehrt", user_id, correlation, ident)
        db.commit()
        return public_run(run)
    except BaseException:
        db.rollback()
        run = get_run(db, ident, user_id)
        run.status, run.active_aktion, run.finished_at = "fehlgeschlagen", None, utcnow()
        # Bereits gelöschte Dateien bleiben mit dem letzten Zwischenstand erhalten.
        run.ergebnis = {**run.ergebnis, "fehler": "Ausfuehrung abgebrochen; Verifikation erforderlich"}
        audit(db, "fehlgeschlagen", user_id, correlation, ident, "ausfuehrung_abgebrochen")
        db.commit()
        raise


def rate_limit(db: Session, user_id: int, correlation: str) -> None:
    # Der Benutzer-Lock serialisiert auch zwischen Hosts. FOR UPDATE liest in
    # MariaDB den aktuellen Bestand statt eines früheren Repeatable-Read-Snapshots.
    db.execute(update(User).where(User.id == user_id).values(session_epoch=User.session_epoch))
    count = len(
        list(
            db.scalars(
                select(AuditLog.id)
                .where(
                    AuditLog.user_id == user_id,
                    AuditLog.action == "admin_repair.angefordert",
                    AuditLog.created_at > utcnow() - timedelta(minutes=1),
                )
                .limit(20)
                .with_for_update()
            )
        )
    )
    if count >= 20:
        raise ValueError("Rate-Limit erreicht")
    audit(db, "angefordert", user_id, correlation)
    db.commit()
