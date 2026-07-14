"""ECPG – serieller Alarm-Ingest (Phase 3).

Idempotenz über raw_hash (verhindert Doppel-Einsätze bei Retries). Einsatz-Anlage
+ Dedup gegen LIS/API über find_matching_incident (Muster _get_or_link_incident).
Nie einen Alarm verschlucken: bei parse_failed wird der Rohtext trotzdem
gespeichert und der Einsatz als unklassifizierter Alarm angelegt.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.gateway import PARSE_FAILED, AlarmIngest

logger = logging.getLogger("einsatzleiter.alarm")

# Alarm-Typ für nicht klassifizierbare serielle Alarme.
FALLBACK_ALARM_CODE = "SONSTIGE"


def raw_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8", "replace")).hexdigest()


def ingest_alarm(
    db: Session,
    *,
    org_id: int,
    gateway_id: int,
    raw_text: str,
    charset: str | None,
    parsed: dict | None,
    parse_status: str,
) -> tuple[AlarmIngest, bool]:
    """Verarbeitet einen seriellen Alarm. Gibt (ingest, created_new) zurück.

    created_new=False ⟺ der raw_hash war schon vorhanden (idempotenter Retry).
    """
    h = raw_hash(raw_text)
    existing = (
        db.query(AlarmIngest)
        .filter(AlarmIngest.raw_hash == h)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if existing is not None:
        return existing, False

    ingest = AlarmIngest(
        org_id=org_id,
        gateway_id=gateway_id,
        raw_hash=h,
        raw_text=raw_text,
        charset=charset,
        parsed=parsed,
        parse_status=parse_status,
        received_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(ingest)
    db.flush()

    incident, action = _create_or_link_incident(db, org_id, parsed, parse_status, raw_text)
    if incident is not None:
        ingest.einsatz_id = incident.id
        ingest.dedup_action = action
    db.flush()
    return ingest, True


def _create_or_link_incident(
    db: Session, org_id: int, parsed: dict | None, parse_status: str, raw_text: str
):
    """Legt einen Einsatz an oder verknüpft mit einem passenden (LIS/API-)Einsatz.

    Gibt (incident|None, action) zurück. action ∈ created|merged.
    """
    from app.services.incident_service import create_incident
    from app.services.lis.lis_matching import find_matching_incident

    p = parsed or {}
    alarm_code = p.get("alarm_type_code") or FALLBACK_ALARM_CODE
    started_at = _parse_dt(p.get("started_at"))
    street = p.get("street")
    city = p.get("city")
    reason = p.get("reason") or (raw_text[:200] if parse_status == PARSE_FAILED else None)

    # Dedup gegen bereits vorhandene (LIS/API) Einsätze
    match = None
    try:
        match = find_matching_incident(
            db, org_id,
            alarm_type_code=alarm_code,
            street=street,
            city=city,
            started_at=started_at,
            report_text=raw_text,
        )
    except Exception as exc:  # Matching darf den Ingest nie blockieren
        logger.warning("Alarm-Matching fehlgeschlagen (org %s): %s", org_id, exc)

    if match is not None:
        logger.info("Serieller Alarm mit vorhandenem Einsatz %s verknüpft (org %s)", match.id, org_id)
        return match, "merged"

    incident = create_incident(
        db,
        alarm_code,
        started_at=started_at,
        is_exercise=bool(p.get("is_exercise")),
        address_street=street,
        address_no=p.get("house_no"),
        address_city=city,
        report_text=raw_text,
        reason=reason,
        primary_org_id=org_id,
    )
    # Herkunft markieren (Feld source falls vorhanden)
    if hasattr(incident, "source"):
        incident.source = "serial"
    db.flush()
    logger.info("Einsatz %s aus seriellem Alarm angelegt (org %s, parse=%s)",
                incident.id, org_id, parse_status)
    return incident, "created"


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, TypeError):
        return None
