"""ECPG – serieller Alarm-Ingest (Phase 3).

Rohdaten werden vor der Verarbeitung separat committed. Einsatz-Anlage
+ Dedup gegen LIS/API über find_matching_incident (Muster _get_or_link_incident).
Nie einen Alarm verschlucken: bei parse_failed wird der Rohtext trotzdem
gespeichert und der Einsatz als unklassifizierter Alarm angelegt.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.gateway import PARSE_FAILED, AlarmIngest
from app.services.alarm_parser import AlarmParseResult, parse_alarm
from app.services.alarm_parser.katalog import baue_kontext
from app.services.alarm_parser.laufzeit import aktiver_regelsatz

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


def process_ingest(
    db: Session, ingest_id: int, *, org_id: int | None = None,
    on_applied: Callable[[AlarmIngest], None] | None = None,
    claim_filter: ColumnElement[bool] | None = None,
) -> AlarmIngest:
    """Verarbeitet eine gespeicherte ID genau einmal; Fehler erhalten die Rohdaten."""
    # Nur die Org-Zuordnung anhand der internen ID ermitteln. Alle folgenden
    # Datensatzabfragen und Mutationen sind explizit auf diese Org eingeschraenkt.
    org_lookup = select(AlarmIngest.org_id).where(AlarmIngest.id == ingest_id)
    if org_id is not None:
        org_lookup = org_lookup.where(AlarmIngest.org_id == org_id)
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
    now = datetime.now(UTC).replace(tzinfo=None)
    claimed = (db.query(AlarmIngest)
               .filter(AlarmIngest.id == ingest_id, AlarmIngest.org_id == org_id,
                       or_(
                           AlarmIngest.process_status.in_(("stored", "error")),
                           and_(AlarmIngest.process_status == "processing", or_(
                               AlarmIngest.lease_until <= now,
                               and_(AlarmIngest.lease_until.is_(None),
                                    AlarmIngest.received_at < now - timedelta(minutes=2)),
                           )),
                       ), *([claim_filter] if claim_filter is not None else []))
               .execution_options(include_all_tenants=True)
               .update({AlarmIngest.process_status: "processing",
                        AlarmIngest.lease_until: now + timedelta(minutes=2),
                        AlarmIngest.parsed_at: now}, synchronize_session=False))
    if claimed != 1:
        return load()
    ingest = load()
    try:
        # Savepoint: bei Fehlern nur die Einsatzanlage zurueckrollen. Der Claim
        # bleibt bis zum Commit gesperrt, auch waehrend wir error protokollieren.
        with db.begin_nested():
            parsed = _parse_ingest(db, ingest, org_id)
            incident, action = _create_or_link_incident(
                db, org_id, parsed, ingest.parse_status, ingest.raw_text,
            )
            if incident is not None:
                ingest.einsatz_id = incident.id
                ingest.dedup_action = action
            ingest.process_status = "applied"
            ingest.applied_at = datetime.now(UTC).replace(tzinfo=None)
            db.flush()
    except Exception as exc:
        logger.exception("Alarm-Ingest %s fehlgeschlagen (org %s)", ingest_id, org_id)
        ingest = load()
        ingest.process_status = "error"
        ingest.process_error = str(exc)
    ingest.process_attempts = (ingest.process_attempts or 0) + 1
    ingest.lease_until = None
    db.commit()
    # Nur der Gewinner des atomaren Claims darf den Nachlauf ausloesen.
    if ingest.process_status == "applied" and on_applied is not None:
        on_applied(ingest)
    return ingest


def _parse_ingest(db: Session, ingest: AlarmIngest, org_id: int) -> dict:
    """Cloud-Ergebnis speichern und kompatible Werte fuer die Einsatzanlage bauen."""
    gespeichert = ingest.parsed or {}
    gateway = gespeichert.get("gateway_parsed") if "gateway_parsed" in gespeichert else ingest.parsed
    fallback = gateway or {}
    ingest.process_error = None
    ingest.parser_version = "0"
    ingest.leitstellennummer = None
    try:
        regeln = aktiver_regelsatz(db, org_id)
        ingest.parser_version = regeln.version
        result = parse_alarm(ingest.raw_text, baue_kontext(db, org_id), regeln)
        if "parser_fehler" in result.hinweise:
            raise ValueError("Parser meldet parser_fehler")
        ingest.leitstellennummer = result.leitstellennummer
        ingest.parsed = {**result.to_dict(), "gateway_parsed": gateway}
        ingest.parse_status = "parsed" if result.status in ("ok", "teilweise") else PARSE_FAILED
        parsed = {
            "alarm_type_code": result.alarm_type_code,
            "street": result.strasse,
            "house_no": result.hausnummer,
            "city": result.gemeinde,
            "started_at": result.alarmzeit,
            "reason": " > ".join(filter(None, [result.meldebild, *result.zusatzinfo])) or None,
            "is_exercise": result.ist_uebung,
        }
        for feld, wert in fallback.items():
            if (result.status == "unklar" or parsed.get(feld) in (None, "", False)) and wert is not None:
                parsed[feld] = wert
        return parsed
    except Exception as exc:
        logger.exception("Cloud-Parser fehlgeschlagen (Ingest %s, org %s)", ingest.id, org_id)
        ingest.process_error = f"Cloud-Parser: {exc}"
        ingest.parse_status = PARSE_FAILED
        fehler = AlarmParseResult(
            original_text=ingest.raw_text, rest_text=ingest.raw_text,
            parser_version=ingest.parser_version, hinweise=["parser_fehler"],
        )
        ingest.parsed = {**fehler.to_dict(), "gateway_parsed": gateway}
        return fallback


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
