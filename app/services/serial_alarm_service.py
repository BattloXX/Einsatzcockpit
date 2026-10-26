"""ECPG – serieller Alarm-Ingest (Phase 3).

Rohdaten werden vor der Verarbeitung separat committed. Einsatz-Anlage
+ Dedup gegen LIS/API über find_matching_incident (Muster _get_or_link_incident).
Nie einen Alarm verschlucken: bei parse_failed wird der Rohtext trotzdem
gespeichert und der Einsatz als unklassifizierter Alarm angelegt.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.gateway import PARSE_FAILED, AlarmIngest

logger = logging.getLogger("einsatzleiter.alarm")

# Alarm-Typ für nicht klassifizierbare serielle Alarme.
FALLBACK_ALARM_CODE = "SONSTIGE"


def raw_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8", "replace")).hexdigest()


def store_raw(
    db: Session,
    *,
    org_id: int,
    gateway_id: int,
    raw_text: str,
    raw_bytes: bytes | None = None,
    charset: str | None = None,
    parsed: dict | None = None,
    parse_status: str,
    sequence: int | None = None,
    received_at_gateway: datetime | None = None,
    source: str = "wut",
) -> tuple[AlarmIngest, bool]:
    """Speichert Rohdaten in einer eigenen Transaktion, ohne Einsatzverarbeitung."""
    h = raw_hash(raw_text)
    now = datetime.now(UTC).replace(tzinfo=None)

    def find_duplicate() -> AlarmIngest | None:
        query = db.query(AlarmIngest).filter(AlarmIngest.org_id == org_id)
        if sequence is not None:
            query = query.filter(AlarmIngest.gateway_id == gateway_id, AlarmIngest.sequence == sequence)
        else:
            query = query.filter(
                AlarmIngest.content_hash == h,
                AlarmIngest.received_at >= now - timedelta(minutes=10),
            )
        return query.execution_options(include_all_tenants=True).first()

    existing = find_duplicate()
    if existing is not None:
        return existing, False

    ingest = AlarmIngest(
        org_id=org_id, gateway_id=gateway_id, raw_hash=h, content_hash=h,
        raw_text=raw_text, raw_bytes=raw_bytes[:65536] if raw_bytes is not None else None,
        charset=charset, parsed=parsed, parse_status=parse_status,
        sequence=sequence, received_at_gateway=_parse_dt(received_at_gateway),
        source=source, received_at=now, process_status="stored", process_attempts=0,
    )
    db.add(ingest)
    try:
        db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = find_duplicate()
        if existing is None:
            raise
        return existing, False
    return ingest, True


def process_ingest(db: Session, ingest_id: int) -> AlarmIngest:
    """Verarbeitet eine gespeicherte ID genau einmal; Fehler erhalten die Rohdaten."""
    # Nur die Org-Zuordnung anhand der internen ID ermitteln. Alle folgenden
    # Datensatzabfragen und Mutationen sind explizit auf diese Org eingeschraenkt.
    org_lookup = select(AlarmIngest.org_id).where(AlarmIngest.id == ingest_id)
    context_org = db.info.get("current_org_id")
    if context_org is not None:
        org_lookup = org_lookup.where(AlarmIngest.org_id == context_org)
    org_id = db.execute(org_lookup.execution_options(include_all_tenants=True)).scalar_one()
    if org_id is None:
        raise ValueError("AlarmIngest ohne Organisation kann nicht verarbeitet werden")

    def load() -> AlarmIngest:
        return (db.query(AlarmIngest)
                .filter(AlarmIngest.id == ingest_id, AlarmIngest.org_id == org_id)
                .execution_options(include_all_tenants=True).populate_existing().one())

    ingest = load()
    claimed = (db.query(AlarmIngest)
               .filter(AlarmIngest.id == ingest_id, AlarmIngest.org_id == org_id,
                       AlarmIngest.process_status.in_(("stored", "error")))
               .execution_options(include_all_tenants=True)
               .update({AlarmIngest.process_status: "processing"}, synchronize_session=False))
    if claimed != 1:
        return load()
    try:
        # Savepoint: bei Fehlern nur die Einsatzanlage zurueckrollen. Der Claim
        # bleibt bis zum Commit gesperrt, auch waehrend wir error protokollieren.
        with db.begin_nested():
            incident, action = _create_or_link_incident(
                db, org_id, ingest.parsed, ingest.parse_status, ingest.raw_text,
            )
            if incident is not None:
                ingest.einsatz_id = incident.id
                ingest.dedup_action = action
            ingest.process_status = "applied"
            ingest.process_error = None
            ingest.applied_at = datetime.now(UTC).replace(tzinfo=None)
            db.flush()
    except Exception as exc:
        logger.exception("Alarm-Ingest %s fehlgeschlagen (org %s)", ingest_id, org_id)
        ingest = load()
        ingest.process_status = "error"
        ingest.process_error = str(exc)
    ingest.process_attempts = (ingest.process_attempts or 0) + 1
    db.commit()
    return ingest


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
    """Kompatibler Einstieg: erst speichern, dann getrennt verarbeiten."""
    ingest, created = store_raw(
        db, org_id=org_id, gateway_id=gateway_id, raw_text=raw_text,
        charset=charset, parsed=parsed, parse_status=parse_status,
    )
    if created:
        ingest = process_ingest(db, ingest.id)
    return ingest, created


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

    # reject_near_duplicates bewusst NICHT gesetzt (Default False): find_matching_incident()
    # oben hat bereits sorgfaeltig geprueft und nichts gefunden - siehe lis_sync.py fuer
    # dieselbe Begruendung.
    incident, _ = create_incident(
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
        return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, TypeError):
        return None
