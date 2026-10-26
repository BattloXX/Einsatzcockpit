"""Auswahl und transaktionale Verwaltung versionierter Parserregeln."""

import logging
from datetime import UTC, datetime
from threading import RLock
from time import monotonic

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.gateway import AlarmParserRegelsatz
from app.models.master import FireDept

from .regelsatz import STANDARD_REGELSATZ, Regelsatz

logger = logging.getLogger("einsatzleiter.alarm")
_cache: dict[int, tuple[float, Regelsatz]] = {}
_lock = RLock()


def invalidiere_regelsatz(org_id: int) -> None:
    with _lock:
        _cache.pop(org_id, None)


def _vormerken(db: Session, org_id: int) -> None:
    db.info.setdefault("alarm_parser_geaendert", set()).add(org_id)
    invalidiere_regelsatz(org_id)


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _transaktion_beendet(db: Session) -> None:
    # Savepoints beenden noch nicht die aeussere Transaktion.
    if db.in_nested_transaction():
        return
    for org_id in db.info.pop("alarm_parser_geaendert", set()):
        invalidiere_regelsatz(org_id)


def lade_regelsatz(db: Session, org_id: int, version: int) -> AlarmParserRegelsatz | None:
    return db.query(AlarmParserRegelsatz).filter(
        AlarmParserRegelsatz.org_id == org_id, AlarmParserRegelsatz.version == version,
    ).one_or_none()


def _validiere(row: AlarmParserRegelsatz) -> Regelsatz:
    if not isinstance(row.regeln, dict) or not row.regeln:
        raise ValueError("Parserregeln fehlen")
    return Regelsatz.model_validate({**row.regeln, "version": str(row.version)})


def aktiver_regelsatz(db: Session, org_id: int) -> Regelsatz:
    pending = org_id in db.info.get("alarm_parser_geaendert", set())
    with _lock:
        entry = _cache.get(org_id)
        if not pending and entry and monotonic() - entry[0] < 60:
            return entry[1].model_copy(deep=True)
        row = db.query(AlarmParserRegelsatz).filter(
            AlarmParserRegelsatz.org_id == org_id, AlarmParserRegelsatz.status == "aktiv",
        ).order_by(AlarmParserRegelsatz.version.desc()).first()
        result = STANDARD_REGELSATZ
        try:
            if row is None:
                raise ValueError("Kein aktiver Regelsatz")
            result = _validiere(row)
        except ValueError as exc:
            logger.warning("Parser-Regelsatz-Fallback (org %s): %s", org_id, exc)
        if not pending:
            _cache[org_id] = (monotonic(), result.model_copy(deep=True))
        return result.model_copy(deep=True)


def _sperre_org(db: Session, org_id: int) -> None:
    # Serialisiert Versionsvergabe und Aktivierung auf MariaDB, auch wenn noch
    # keine Regelzeile existiert. UNIQUE(org_id, version) sichert zudem SQLite.
    db.query(FireDept).filter(FireDept.id == org_id).with_for_update().one()


def lege_entwurf_an(
    db: Session, org_id: int, regeln: dict | None = None, *,
    notiz: str | None = None, basis_version: int | None = None,
    erstellt_von_user_id: int | None = None, quelle: str = "ui",
) -> AlarmParserRegelsatz:
    """Legt einen validierten Entwurf an; Commit bleibt beim Aufrufer."""
    if quelle not in ("ui", "mcp"):
        raise ValueError("Unbekannte Quelle")
    _sperre_org(db, org_id)
    if regeln is None:
        basis = lade_regelsatz(db, org_id, basis_version) if basis_version is not None else None
        if basis_version is not None and basis is None:
            raise ValueError("Basisversion nicht gefunden")
        kopie = _validiere(basis) if basis else aktiver_regelsatz(db, org_id)
        basis_version = int(kopie.version)
        regeln = kopie.model_dump(mode="json")
    validiert = Regelsatz.model_validate(regeln)
    letzte = db.query(AlarmParserRegelsatz.version).filter(
        AlarmParserRegelsatz.org_id == org_id,
    ).order_by(AlarmParserRegelsatz.version.desc()).with_for_update().first()
    version = letzte[0] + 1 if letzte else 1
    row = AlarmParserRegelsatz(
        org_id=org_id, version=version, status="entwurf",
        regeln=validiert.model_dump(mode="json"), notiz=notiz, basis_version=basis_version,
        erstellt_von_user_id=erstellt_von_user_id, quelle=quelle,
    )
    db.add(row)
    db.flush()
    return row


def aktiviere_regelsatz(
    db: Session, org_id: int, version: int, *, aktiviert_von_user_id: int | None = None,
) -> AlarmParserRegelsatz:
    """Aktiviert nur schema-validierte Regeln; Probelauf/Testfaelle folgen in P5."""
    _sperre_org(db, org_id)
    row = lade_regelsatz(db, org_id, version)
    if row is None:
        raise ValueError("Regelsatz nicht gefunden")
    _validiere(row)
    for alt in db.query(AlarmParserRegelsatz).filter(
        AlarmParserRegelsatz.org_id == org_id, AlarmParserRegelsatz.status == "aktiv",
    ).with_for_update().all():
        alt.status = "archiviert"
    row.status = "aktiv"
    row.aktiviert_am = datetime.now(UTC).replace(tzinfo=None)
    row.aktiviert_von_user_id = aktiviert_von_user_id
    _vormerken(db, org_id)
    db.flush()
    return row
