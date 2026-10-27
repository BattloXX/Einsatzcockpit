"""Nachlauf nach erfolgreicher Anlage eines seriellen Alarms."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import BackgroundTasks
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.models.gateway import PARSE_FAILED, AlarmIngest
from app.models.incident import Incident
from app.models.incident_alarm_field import IncidentAlarmField
from app.models.user import User

logger = logging.getLogger("einsatzleiter.alarm")


ALARM_FELDER = ("alarm_type_code", "address_street", "address_no", "address_city", "report_text", "reason")


@dataclass
class MergeErgebnis:
    felder_geaendert: list[str] = field(default_factory=list)
    adresse_geaendert: bool = False
    stichwort_erhoeht: bool = False
    neue_codes: list[str] = field(default_factory=list)


def _herkunft(db: Session, incident_id: int, org_id: int, feld: str) -> IncidentAlarmField | None:
    return db.query(IncidentAlarmField).filter(
        IncidentAlarmField.org_id == org_id, IncidentAlarmField.incident_id == incident_id,
        IncidentAlarmField.field == feld,
    ).first()


def _speichere_feld(
    db: Session, incident_id: int, org_id: int, feld: str, wert: str | None,
    source: str, ingest_id: int | None,
) -> None:
    eintrag = _herkunft(db, incident_id, org_id, feld)
    if eintrag is None:
        eintrag = IncidentAlarmField(incident_id=incident_id, org_id=org_id, field=feld)
        db.add(eintrag)
    eintrag.value = wert
    eintrag.source = source
    eintrag.ingest_id = ingest_id
    eintrag.updated_at = datetime.now(UTC).replace(tzinfo=None)


def markiere_manuell(db: Session, incident_id: int, org_id: int, field: str) -> None:
    """Zentrale Audit-Anbindung; auch bewusst geleerte Felder bleiben geschuetzt."""
    incident = db.query(Incident).filter(
        Incident.id == incident_id, Incident.primary_org_id == org_id,
    ).first()
    if incident is None or field not in ALARM_FELDER:
        return
    _speichere_feld(db, incident_id, org_id, field, getattr(incident, field), "manual", None)


def _codes(parsed: dict) -> list[str]:
    return list(dict.fromkeys(str(code).lower() for code in
                             [*parsed.get("weitere_codes", []), *parsed.get("sonderkennzeichen", [])]))


def merge_folgenachricht(
    db: Session, incident: Incident, ingest: AlarmIngest, parsed_result: dict, org_id: int,
    *, nur_alarm: bool = False,
) -> MergeErgebnis:
    """Fuehrt eine Version zusammen; commit und Nachlauf bleiben beim Aufrufer."""
    from app.core.audit import write_incident_change
    from app.core.timezones import format_local_datetime
    from app.models.incident import IncidentLog
    from app.models.master import AlarmType, FireDept

    if incident.primary_org_id != org_id or ingest.org_id != org_id:
        raise ValueError("Alarm-Merge ausserhalb der Organisation")
    result = MergeErgebnis()
    version = ingest.version_no
    now = datetime.now(UTC).replace(tzinfo=None)

    def journal(text: str, *, einmalig: bool = False) -> None:
        if einmalig and db.query(IncidentLog.id).filter(
            IncidentLog.incident_id == incident.id, IncidentLog.text == text,
        ).first() is not None:
            return
        db.add(IncidentLog(incident_id=incident.id, level="info", text=text, ts=now))

    def hinweis(feld: str, wert: str) -> None:
        journal(f"Abweichender Alarmwert fuer {feld}: {wert}", einmalig=True)

    def geschuetzt(feld: str, wert: str) -> bool:
        herkunft = _herkunft(db, incident.id, org_id, feld)
        if herkunft is not None and herkunft.source == "manual":
            hinweis(feld, wert)
            return True
        return nur_alarm and (herkunft is None or herkunft.source != "alarm")

    def aendern(feld: str, wert: str, text: str) -> None:
        vorher = getattr(incident, feld)
        setattr(incident, feld, wert)
        _speichere_feld(db, incident.id, org_id, feld, wert, "alarm", ingest.id)
        write_incident_change(db, incident.id, "alarm.update", "incident", incident.id,
                              {feld: vorher}, {feld: wert}, ts=now)
        journal(f"Alarmupdate v{version}: {text}")
        result.felder_geaendert.append(feld)

    for feld, key in (("address_street", "street"), ("address_no", "house_no"), ("address_city", "city")):
        wert = parsed_result.get(key)
        vorher = getattr(incident, feld)
        if not wert or wert == vorher or geschuetzt(feld, wert):
            continue
        herkunft = _herkunft(db, incident.id, org_id, feld)
        if not vorher or (herkunft is not None and herkunft.source == "alarm"):
            aendern(feld, wert, f"{feld} {vorher or '-'} -> {wert}")
            result.adresse_geaendert = True
        else:
            hinweis(feld, wert)

    # Das bestehende Geocoding setzt nur leere Koordinaten. Nach einer neuen
    # Adresse duerfen keine Koordinaten der alten Adresse weiterverwendet werden.
    if result.adresse_geaendert:
        for feld in ("lat", "lng"):
            vorher = getattr(incident, feld)
            if vorher is not None:
                setattr(incident, feld, None)
                write_incident_change(db, incident.id, "alarm.update", "incident", incident.id,
                                      {feld: vorher}, {feld: None}, ts=now)
                journal(f"Alarmupdate v{version}: {feld} nach Adressaenderung zurueckgesetzt")
                result.felder_geaendert.append(feld)

    code = parsed_result.get("alarm_type_code")
    alt = incident.alarm_type_code
    if code and code != alt and not geschuetzt("alarm_type_code", code):
        # AlarmType hat keine Stufe/Sortierung. Kategorie aus Org-Stammdaten,
        # Stufe aus dem numerischen Code; unbekannte Codes nur gleicher Praefix.
        typen = {t.code.upper(): t for t in db.query(AlarmType).filter(AlarmType.org_id == org_id).all()}
        vorher_match = re.fullmatch(r"([A-Z]+)(\d+)", (alt or "").upper())
        nachher_match = re.fullmatch(r"([A-Z]+)(\d+)", code.upper())
        vergleichbar = bool(vorher_match and nachher_match and vorher_match[1] == nachher_match[1])
        if alt and alt.upper() in typen and code.upper() in typen:
            vergleichbar = vergleichbar and typen[alt.upper()].category == typen[code.upper()].category
        if vergleichbar and vorher_match and nachher_match and int(nachher_match[2]) > int(vorher_match[2]):
            aendern("alarm_type_code", code, f"Stichwort {alt} -> {code}")
            result.stichwort_erhoeht = True
        else:
            hinweis("alarm_type_code", code)

    # reason bleibt der urspruengliche Einsatzgrund. Ergaenzungen stehen nur im
    # Meldebild, damit aus mehreren Versionen keine widerspruechlichen Gruende werden.
    reason = parsed_result.get("reason")
    if reason and reason != incident.reason:
        geschuetzt("reason", reason)
    teile = [parsed_result.get("meldebild"), *parsed_result.get("zusatzinfo", [])]
    if not any(teile):
        teile = [parsed_result.get("reason")]
    neu: list[str] = []
    for teil in teile:
        if teil and teil not in (incident.report_text or "") and teil not in neu:
            neu.append(teil)
    if neu:
        text = "\n".join(neu)
        if not geschuetzt("report_text", text):
            org = db.query(FireDept).filter(FireDept.id == org_id).one()
            datum = format_local_datetime(now, org)
            aendern("report_text", (incident.report_text or "") + f"\n\n--- Nachtrag v{version} {datum} ---\n{text}",
                    f"Meldebild ergaenzt: {text}")

    gesehen = _herkunft(db, incident.id, org_id, "codes")
    bisher = json.loads(gesehen.value) if gesehen and gesehen.value else []
    result.neue_codes = [code for code in _codes(parsed_result) if code not in bisher]
    if result.neue_codes and (not nur_alarm or not geschuetzt("codes", ", ".join(result.neue_codes))):
        wert = json.dumps([*bisher, *result.neue_codes])
        _speichere_feld(db, incident.id, org_id, "codes", wert, "alarm", ingest.id)
        write_incident_change(db, incident.id, "alarm.update", "incident", incident.id,
                              {"codes": bisher}, {"codes": json.loads(wert)}, ts=now)
        journal(f"Alarmupdate v{version}: Codes ergaenzt: {', '.join(result.neue_codes)}")
        result.felder_geaendert.append("codes")
    return result


def verarbeite_neu(db: Session, ingest: AlarmIngest, org_id: int, user: User) -> AlarmIngest:
    """Aktuellen Parser anwenden, verknuepfte Einsaetze ohne Alarm-/Drucknachlauf mergen."""
    from app.models.incident import IncidentLog
    from app.services.serial_alarm_service import _parse_ingest, process_ingest

    if ingest.org_id != org_id or user.org_id != org_id:
        raise ValueError("Wiederverarbeitung ausserhalb der Organisation")
    ingest = db.query(AlarmIngest).filter(
        AlarmIngest.id == ingest.id, AlarmIngest.org_id == org_id,
    ).populate_existing().with_for_update().one()
    if ingest.process_status == "processing":
        raise ValueError("Datensatz wird bereits verarbeitet")
    text = f"Alarmdaten manuell neu verarbeitet von {user.username}"
    if ingest.einsatz_id is None:
        ingest.process_status = "error"
        db.flush()
        ingest = process_ingest(db, ingest.id, org_id=org_id)
        if ingest.process_status == "applied" and ingest.einsatz_id:
            db.add(IncidentLog(incident_id=ingest.einsatz_id, author_name=user.display_name or user.username,
                               level="info", text=text))
            db.commit()
        return ingest

    incident = db.query(Incident).filter(
        Incident.id == ingest.einsatz_id, Incident.primary_org_id == org_id,
    ).with_for_update().one()
    vorher = (ingest.parsed, ingest.parser_version, ingest.process_status, ingest.process_error)
    incident_geaendert = False
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        with db.begin_nested():
            parsed = _parse_ingest(db, ingest, org_id)
            if ingest.process_error:
                raise ValueError(ingest.process_error)
            ergebnis = merge_folgenachricht(
                db, incident, ingest, {**(ingest.parsed or {}), **parsed}, org_id, nur_alarm=True,
            )
            incident_geaendert = bool(ergebnis.felder_geaendert)
            ingest.process_status = "applied"
            vorhanden = db.query(IncidentLog.id).filter(
                IncidentLog.incident_id == incident.id, IncidentLog.text == text,
            ).first()
            geaendert = vorher != (ingest.parsed, ingest.parser_version, ingest.process_status, ingest.process_error)
            if geaendert or ergebnis.felder_geaendert or vorhanden is None:
                db.add(IncidentLog(incident_id=incident.id, author_name=user.display_name or user.username,
                                   level="info", text=text, ts=now))
                ingest.parsed_at = now
                ingest.applied_at = now
                ingest.process_attempts = (ingest.process_attempts or 0) + 1
            db.flush()
    except Exception as exc:
        logger.exception("Manuelle Wiederverarbeitung fehlgeschlagen (ingest %s, org %s)", ingest.id, org_id)
        ingest.process_status = "error"
        ingest.process_error = str(exc)
        ingest.process_attempts = (ingest.process_attempts or 0) + 1
        ingest.parsed_at = now
    db.commit()
    if incident_geaendert and ingest.process_status == "applied":
        from app.services.broadcast import broadcast_org, manager
        payload = {"type": "incident_updated", "incident_id": incident.id}
        try:
            run_async(broadcast_org, org_id, payload)
            run_async(manager.broadcast, incident.id, payload)
        except Exception:
            logger.warning("Manueller Alarmupdate-Broadcast fehlgeschlagen (ingest %s, org %s)",
                           ingest.id, org_id, exc_info=True)
    return ingest


def melde_ingest_geaendert(ingest: AlarmIngest) -> None:
    """Diagnoseansichten workeruebergreifend informieren; Speicherung bleibt unabhaengig."""
    from app.services.broadcast import broadcast_org

    try:
        run_async(broadcast_org, ingest.org_id, {
            "type": "alarm_ingest_changed", "gateway_id": ingest.gateway_id, "ingest_id": ingest.id,
        })
    except Exception:
        logger.warning("Alarm-Diagnose-Broadcast fehlgeschlagen (ingest %s, org %s)",
                       ingest.id, ingest.org_id, exc_info=True)


def nach_adressaenderung(incident_id: int, background_tasks: BackgroundTasks) -> None:
    """Geocoding und Objektmatching nach dem Verarbeitungscommit einreihen."""
    background_tasks.add_task(_geocode_adressaenderung, incident_id)
    from app.services.objekt_matching_service import match_incident_background
    background_tasks.add_task(match_incident_background, incident_id)


async def _geocode_adressaenderung(incident_id: int) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.routers.api_v1 import _geocode_incident

    with SessionLocal() as db:
        set_tenant_context(db, None)
        incident = db.get(Incident, incident_id)
        if incident is not None and (incident.address_street or incident.address_city):
            await _geocode_incident(incident.id, incident.address_street, incident.address_no, incident.address_city)


def wende_an(db: Session, ingest: AlarmIngest, org_id: int) -> tuple[Incident | None, str]:
    """Ordnet einen Cloud-Alarm zu und fuehrt Folgenachrichten zusammen."""
    from app.services.incident_service import create_incident
    from app.services.lis.lis_matching import find_matching_incident
    from app.services.serial_alarm_service import FALLBACK_ALARM_CODE, _parse_dt, _parse_ingest

    p = _parse_ingest(db, ingest, org_id)
    nummer = ingest.leitstellennummer
    alarm_code = p.get("alarm_type_code") or FALLBACK_ALARM_CODE
    started_at = _parse_dt(p.get("started_at"))
    street, city = p.get("street"), p.get("city")
    reason = p.get("reason") or (ingest.raw_text[:200] if ingest.parse_status == PARSE_FAILED else None)

    def nummer_treffer() -> Incident | None:
        # Locking Read sieht unter MariaDB REPEATABLE READ auch den Gewinner
        # eines parallelen Inserts, ohne den aeusseren Claim zurueckzurollen.
        return db.query(Incident).filter(
            Incident.primary_org_id == org_id, Incident.lis_operation_number == nummer,
        ).populate_existing().with_for_update().first()

    match = None
    try:
        match = find_matching_incident(
            db, org_id, alarm_type_code=alarm_code, street=street, city=city,
            started_at=started_at, report_text=ingest.raw_text,
            lis_operation_number=nummer,
        )
    except Exception as exc:  # Matching darf den Ingest nie blockieren
        logger.warning("Alarm-Matching fehlgeschlagen (org %s): %s", org_id, exc)

    action = "merged"
    if match is None:
        try:
            # Auch Tokens, Spalten und Audit der unterlegenen Anlage zurueckrollen.
            with db.begin_nested():
                match, _ = create_incident(
                    db, alarm_code, started_at=started_at,
                    is_exercise=bool(p.get("is_exercise")), address_street=street,
                    address_no=p.get("house_no"), address_city=city,
                    report_text=ingest.raw_text, reason=reason, primary_org_id=org_id,
                )
                match.lis_operation_number = nummer
                if hasattr(match, "source"):
                    match.source = "serial"
                db.flush()
            action = "created"
        except IntegrityError:
            if not nummer:
                raise
            match = nummer_treffer()
            if match is None:
                raise
            logger.info("Alarm-Anlage-Race zur Nummer %s (org %s): Einsatz %s", nummer, org_id, match.id)

    # Inhaltsdedup ohne Zeitfenster, nur innerhalb desselben Einsatzes und Tenants.
    original = None
    if nummer and ingest.content_hash:
        original = db.query(AlarmIngest).filter(
            AlarmIngest.org_id == org_id, AlarmIngest.einsatz_id == match.id,
            AlarmIngest.leitstellennummer == nummer,
            AlarmIngest.content_hash == ingest.content_hash,
            AlarmIngest.id != ingest.id, AlarmIngest.process_status == "applied",
        ).order_by(AlarmIngest.id).first()
    if original is not None:
        ingest.einsatz_id = match.id
        ingest.version_no = original.version_no
        logger.info("Identischer Alarm-Inhalt (ingest %s, Einsatz %s, org %s)", ingest.id, match.id, org_id)
        return match, "duplicate"

    if nummer and not match.lis_operation_number:
        try:
            with db.begin_nested():
                match.lis_operation_number = nummer
                db.flush()
        except IntegrityError:
            logger.info("Leitstellennummer %s bereits belegt (org %s), Zuordnung bleibt", nummer, org_id)

    version = db.query(func.max(AlarmIngest.version_no)).filter(
        AlarmIngest.org_id == org_id, AlarmIngest.einsatz_id == match.id,
        AlarmIngest.id != ingest.id,
    ).scalar()
    # Absichtlich kein UNIQUE: parallele Folgenachrichten duerfen dieselbe Version erhalten.
    ingest.einsatz_id = match.id
    ingest.version_no = (version or 0) + 1
    parsed_result = {**(ingest.parsed or {}), **p}
    if action == "created":
        for feld in ALARM_FELDER:
            _speichere_feld(db, match.id, org_id, feld, getattr(match, feld), "alarm", ingest.id)
        _speichere_feld(db, match.id, org_id, "codes", json.dumps(_codes(parsed_result)), "alarm", ingest.id)
    elif nummer:
        # Transientes Ergebnis bleibt ueber den Commit fuer Router/Recovery verfuegbar.
        ergebnis = merge_folgenachricht(db, match, ingest, parsed_result, org_id)
        ingest.merge_ergebnis = ergebnis  # type: ignore[attr-defined]
        if ergebnis.neue_codes or ergebnis.stichwort_erhoeht:
            from app.models.incident import IncidentLog
            from app.services.alarm_outbox import enqueue_nachalarmierung

            if match.status == "closed":
                db.add(IncidentLog(
                    incident_id=match.id, author_name="System", level="info",
                    text="Nachalarmierung unterdrueckt: Einsatz abgeschlossen",
                ))
            else:
                ausloeser = [("code", ergebnis.neue_codes)] if ergebnis.neue_codes else []
                if ergebnis.stichwort_erhoeht:
                    ausloeser.append(("stichwort", [match.alarm_type_code]))
                for namespace, codes in ausloeser:
                    if enqueue_nachalarmierung(
                        db, match, org_id=org_id, key=f"{namespace}:{'+'.join(sorted(codes))}",
                        codes=codes, source="gateway", base_url=None, push_url=None,
                    ):
                        db.add(IncidentLog(
                            incident_id=match.id, author_name="System", level="info",
                            text=f"Nachalarmierung: Codes {', '.join(codes)}",
                        ))
    logger.info("Serieller Alarm %s: Einsatz %s, Version %s (org %s)", action, match.id, ingest.version_no, org_id)
    return match, action


def nachlauf(
    db: Session, ingest: AlarmIngest, *, org_id: int,
    background_tasks: BackgroundTasks, base_url: str,
) -> None:
    """Outbox, Benachrichtigungen und Hintergrundtasks in bisheriger Reihenfolge."""
    # Erste serielle Nachricht zu einem schon vorhandenen (LIS/API-)Einsatz durchlaeuft
    # weiter den vollen Nachlauf (Outbox ist idempotent); erst Folgeversionen sind Updates.
    folgeversion = (getattr(ingest, "version_no", None) or 1) > 1
    if ingest.dedup_action == "duplicate" or (ingest.dedup_action == "merged" and folgeversion):
        ergebnis = getattr(ingest, "merge_ergebnis", None)
        if ingest.dedup_action == "merged" and ergebnis is not None:
            if ergebnis.adresse_geaendert and ingest.einsatz_id:
                nach_adressaenderung(ingest.einsatz_id, background_tasks)
            from app.services.broadcast import broadcast_org, manager
            payload = {"type": "incident_updated", "incident_id": ingest.einsatz_id}
            try:
                run_async(broadcast_org, org_id, payload)
                run_async(manager.broadcast, ingest.einsatz_id, payload)
            except Exception:
                logger.warning("Alarmupdate-Broadcast fehlgeschlagen (ingest %s, org %s)",
                               ingest.id, org_id, exc_info=True)
        return
    if ingest.einsatz_id:
        from app.models.incident import Incident
        from app.services.alarm_outbox import enqueue_incident_alarm

        incident = db.query(Incident).filter(
            Incident.id == ingest.einsatz_id, Incident.primary_org_id == org_id,
        ).first()
        if incident is not None:
            enqueue_incident_alarm(
                db, incident, org_id=org_id, source="gateway",
                push_url=f"/einsatz/{incident.id}", base_url=base_url,
            )
    db.commit()

    from app.services.print_dispatcher import autoprint_alarm_background
    background_tasks.add_task(autoprint_alarm_background, ingest.id)

    # Board/Infoscreen live informieren + Objekt-Verknüpfung nachziehen.
    # Wie bei UI-/API-/LIS-Anlage: Geocoding (setzt Koordinaten) und Objekt-Matching
    # (BMA-Nr./Adresse; Geo-Stufe folgt nach dem Geocoding) als Background-Task.
    # Ohne das wurden bei Gateway-Alarmen nie Objekte automatisch verknüpft.
    if ingest.einsatz_id:
        from app.models.incident import Incident
        inc = db.query(Incident).filter(
            Incident.id == ingest.einsatz_id, Incident.primary_org_id == org_id,
        ).first()
        if inc is not None:
            from app.services.incident_notify import incident_alarm_started, notify_incident_created
            if not incident_alarm_started(db, inc.id):
                run_async(
                    notify_incident_created, db, inc,
                    org_id=org_id, triggered_by_user_id=None,
                    push_url=f"/einsatz/{inc.id}", base_url=base_url,
                    background_tasks=background_tasks, source="gateway",
                )
            try:
                from app.services.broadcast import broadcast_org
                from app.services.exercise_guard import darf_extern
                run_async(broadcast_org, org_id, {
                        "type": "incident_created",
                        "incident_id": ingest.einsatz_id,
                        "alarm": inc.alarm_type_code,
                        "alarm_erlaubt": darf_extern(
                            "ws_alarm",
                            is_exercise=inc.is_exercise,
                            org_id=org_id,
                            db=db,
                            planspiel_run_id=inc.planspiel_run_id,
                        ),
                        "alarm_type_code": inc.alarm_type_code,
                        "is_exercise": inc.is_exercise,
                    })
            except Exception:
                logger.warning("Alarm-Broadcast fehlgeschlagen (ingest %s, org %s)",
                               ingest.id, org_id, exc_info=True)
            if inc.lat is None and (inc.address_street or inc.address_city):
                from app.routers.api_v1 import _geocode_incident
                background_tasks.add_task(
                    _geocode_incident, inc.id,
                    inc.address_street, inc.address_no, inc.address_city,
                )
            from app.services.objekt_matching_service import match_incident_background
            background_tasks.add_task(match_incident_background, inc.id)
            if ingest.dedup_action == "created":
                from app.services.print_dispatcher import autoprint_incident_background
                background_tasks.add_task(autoprint_incident_background, inc.id)

