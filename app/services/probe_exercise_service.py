"""Sichere Bruecke zwischen einer Probe und dem bestehenden Einsatzsystem."""

from __future__ import annotations

import shutil
from datetime import UTC
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.timezones import now_local
from app.models.incident import Incident, IncidentLog, Message, MessageMedia
from app.models.probenplanung import ProbeMedia
from app.models.teilnahme import Teilnahme, TeilnahmeStatus, Termin
from app.models.user import User
from app.services.incident_service import create_incident
from app.services.probe_checklist_service import ERLAUBTE_STATUSWECHSEL
from app.services.probe_history import write_probe_change
from app.services.probe_media_service import probe_media_path, probe_thumb_path
from app.services.storage_service import reserve_storage

UEBERNAHME_OPTIONEN = frozenset({"objekt_adresse", "alarmtext", "gefahren_hinweise", "skizze", "dokumente"})


def doppelanlage_pruefen(termin: Termin, db: Session | None = None) -> Incident | None:
    """Liefert den noch existierenden verknuepften Einsatz, andernfalls ``None``."""
    if termin.exercise_incident_id is None:
        return None
    session = db or Session.object_session(termin)
    return session.get(Incident, termin.exercise_incident_id) if session is not None else None


def _adresse(db: Session, termin: Termin) -> tuple[str | None, str | None, str | None]:
    if termin.objekt_id is not None:
        from app.models.objekt import Objekt

        objekt = db.get(Objekt, termin.objekt_id)
        if objekt is not None and objekt.org_id == termin.org_id:
            return objekt.strasse, objekt.hausnummer, objekt.ort
    return termin.objekt or None, None, termin.ort or None


def _hinweise_anlegen(db: Session, incident: Incident, termin: Termin, user: User) -> None:
    teile = []
    if termin.besondere_gefahren:
        teile.append(f"Besondere Gefahren: {termin.besondere_gefahren}")
    if termin.besondere_hinweise:
        teile.append(f"Besondere Hinweise: {termin.besondere_hinweise}")
    if teile:
        db.add(IncidentLog(incident_id=incident.id, user_id=user.id, text="\n".join(teile)))


def _medium_kopieren(db: Session, medium: ProbeMedia, message: Message, user: User) -> None:
    """Erzeugt eine physische, einsatzgebundene Kopie; die Probe-Datei bleibt bestehen."""
    source = probe_media_path(medium)
    if not source.is_file():
        return
    root = Path(settings.MEDIA_STORAGE_DIR).resolve()
    destination = root / str(medium.org_id) / str(message.incident_id) / "msg" / str(message.id)
    destination.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix or (".pdf" if medium.kind == "pdf" else ".jpg")
    target = destination / f"{uuid4().hex}{suffix}"
    shutil.copy2(source, target)
    thumb_target: Path | None = None
    source_thumb = probe_thumb_path(medium)
    if source_thumb is not None and source_thumb.is_file():
        thumb_target = destination / f"{uuid4().hex}_thumb{source_thumb.suffix or '.jpg'}"
        shutil.copy2(source_thumb, thumb_target)
    size = target.stat().st_size
    if medium.org_id is None:
        target.unlink(missing_ok=True)
        if thumb_target:
            thumb_target.unlink(missing_ok=True)
        return
    reserve_storage(db, medium.org_id, size)
    db.add(MessageMedia(
        message_id=message.id,
        incident_id=message.incident_id,
        uploaded_by_user_id=user.id,
        kind=medium.kind,
        original_filename=medium.name,
        storage_path=str(target.resolve().relative_to(root)).replace("\\", "/"),
        thumb_path=(
            str(thumb_target.resolve().relative_to(root)).replace("\\", "/") if thumb_target else None
        ),
        mime_type=medium.mime_type,
        bytes=size,
    ))


def _medien_kopieren(db: Session, incident: Incident, termin: Termin, user: User, optionen: set[str]) -> int:
    arten = []
    if "skizze" in optionen:
        arten.append("skizze")
    if "dokumente" in optionen:
        arten.extend(("dokument", "bild"))
    if not arten:
        return 0
    medien = db.query(ProbeMedia).filter(ProbeMedia.termin_id == termin.id, ProbeMedia.art.in_(arten)).all()
    if not medien:
        return 0
    message = Message(incident_id=incident.id, title="Unterlagen aus der Probe", status="information")
    db.add(message)
    db.flush()
    for medium in medien:
        _medium_kopieren(db, medium, message, user)
    return len(medien)


def uebungseinsatz_erstellen(
    db: Session,
    termin: Termin,
    user: User,
    *,
    uebernehmen: set[str],
    request,
    alarm_type_code: str = "T1",
    weiterer: bool = False,
) -> Incident:
    """Legt einen eindeutig als Uebung markierten Einsatz aus einer Probe an."""
    existing = doppelanlage_pruefen(termin, db)
    if existing is not None and not weiterer:
        return existing
    optionen = set(uebernehmen) & UEBERNAHME_OPTIONEN
    street = number = city = None
    if "objekt_adresse" in optionen:
        street, number, city = _adresse(db, termin)
    started_at = now_local(user.org).astimezone(UTC).replace(tzinfo=None)
    incident, _created = create_incident(
        db,
        alarm_type_code,
        started_at=started_at,
        is_exercise=True,
        address_street=street,
        address_no=number,
        address_city=city,
        report_text=termin.alarmtext if "alarmtext" in optionen else None,
        reason=termin.thema or termin.titel,
        incident_leader_user_id=user.id,
        primary_org_id=user.org_id,
        ip=request.client.host if request.client else None,
    )
    if "gefahren_hinweise" in optionen:
        _hinweise_anlegen(db, incident, termin, user)
    copied = _medien_kopieren(db, incident, termin, user, optionen)
    previous = termin.exercise_incident_id
    termin.exercise_incident_id = incident.id
    write_probe_change(
        db, termin.id, "probe.uebungseinsatz_angelegt", "uebungseinsatz", "exercise_incident_id",
        {"exercise_incident_id": previous} if previous else None,
        {"exercise_incident_id": incident.id, "uebernommen": sorted(optionen), "medien": copied},
        user_id=user.id, ip=request.client.host if request.client else None,
    )
    write_audit(
        db, "probe.exercise_incident_created", org_id=user.org_id, user_id=user.id,
        incident_id=incident.id, entity_type="termin", entity_id=termin.id,
        payload={"uebernommen": sorted(optionen), "weiterer": weiterer, "medien": copied},
        ip=request.client.host if request.client else None,
    )
    db.flush()
    return incident


def einsatzstart_synchronisieren(db: Session, incident: Incident, user: User | None = None) -> Termin | None:
    termin = db.query(Termin).filter(Termin.exercise_incident_id == incident.id).first()
    if termin is None:
        return None
    vorher = termin.status
    if "durchfuehrung_laeuft" not in ERLAUBTE_STATUSWECHSEL.get(vorher, frozenset()):
        return termin
    termin.status = "durchfuehrung_laeuft"
    if vorher != termin.status:
        write_probe_change(db, termin.id, "probe.uebungseinsatz_gestartet", "status", "status",
                           {"status": vorher}, {"status": termin.status}, user_id=user.id if user else None)
    db.flush()
    return termin


def einsatzabschluss_synchronisieren(db: Session, incident: Incident, user_id: int | None = None) -> Termin | None:
    termin = db.query(Termin).filter(Termin.exercise_incident_id == incident.id).first()
    if termin is None:
        return None
    vorher = termin.status
    if "durchgefuehrt" not in ERLAUBTE_STATUSWECHSEL.get(vorher, frozenset()):
        return termin
    termin.status = "durchgefuehrt"
    if vorher != termin.status:
        write_probe_change(db, termin.id, "probe.uebungseinsatz_abgeschlossen", "status", "status",
                           {"status": vorher}, {"status": termin.status}, user_id=user_id)
    db.commit()
    return termin


def teilnehmer_uebernehmen(db: Session, termin: Termin, user: User) -> int:
    incident = doppelanlage_pruefen(termin, db)
    if incident is None:
        raise ValueError("Kein Uebungseinsatz verknuepft")
    einsatz_rows = db.query(Teilnahme).filter(
        Teilnahme.bezug_typ == "einsatz", Teilnahme.bezug_id == incident.id,
        Teilnahme.mitglied_id.is_not(None),
    ).all()
    count = 0
    for source in einsatz_rows:
        target = db.query(Teilnahme).filter(
            Teilnahme.bezug_typ == termin.typ, Teilnahme.bezug_id == termin.id,
            Teilnahme.mitglied_id == source.mitglied_id,
        ).first()
        if target is None:
            target = Teilnahme(org_id=termin.org_id, bezug_typ=termin.typ, bezug_id=termin.id,
                               mitglied_id=source.mitglied_id, hinzugefuegt_von=user.id)
            db.add(target)
        target.set_status(TeilnahmeStatus.ANWESEND)
        count += 1
    write_probe_change(db, termin.id, "probe.teilnehmer_aus_einsatz", "teilnehmer", None,
                       None, {"anzahl": count}, user_id=user.id)
    db.flush()
    return count
