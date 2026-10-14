"""Geschäftslogik für einsatzstellenübergreifende Lageinfos."""
from sqlalchemy.orm import Session

from app.models.major_incident import (
    CROSS_MARKER_STATUS_LABEL,
    CROSS_MARKER_TYPE_LABEL,
    CrossMarkerLogEntry,
    CrossSiteMarker,
    MajorIncident,
)


def marker(lage: MajorIncident, db: Session, marker_id: int) -> CrossSiteMarker:
    value = db.get(CrossSiteMarker, marker_id)
    if not value or value.major_incident_id != lage.id:
        raise LookupError("Übergreifende Lageinfo nicht gefunden.")
    return value


def anlegen(db: Session, lage: MajorIncident, *, title: str, marker_type: str = "sonstiges",
            status: str = "aktiv", description: str = "", strasse: str = "", hausnr: str = "",
            ort: str = "", lat: float | None = None, lng: float | None = None,
            user_id: int | None = None, author_name: str | None = None, source: str = "manual") -> CrossSiteMarker:
    value = CrossSiteMarker(major_incident_id=lage.id, org_id=lage.org_id, title=title.strip()[:160],
        marker_type=marker_type if marker_type in CROSS_MARKER_TYPE_LABEL else "sonstiges",
        status=status if status in CROSS_MARKER_STATUS_LABEL else "aktiv", description=description.strip() or None,
        strasse=strasse.strip()[:160] or None, hausnr=hausnr.strip()[:20] or None, ort=ort.strip()[:120] or None,
        lat=lat, lng=lng, created_by=user_id, author_name=author_name, source=source)
    db.add(value)
    return value


def setze_status(db: Session, lage: MajorIncident, marker_id: int, status: str) -> CrossSiteMarker:
    value = marker(lage, db, marker_id)
    if status in CROSS_MARKER_STATUS_LABEL:
        value.status = status
    return value


def bearbeiten(db: Session, lage: MajorIncident, marker_id: int, **felder) -> CrossSiteMarker:
    value = marker(lage, db, marker_id)
    for name, length in (("title", 160), ("description", None), ("strasse", 160), ("hausnr", 20), ("ort", 120)):
        if name in felder:
            raw = (felder[name] or "").strip()
            gekuerzt = raw[:length] if length else raw
            # Titel bleibt Pflichtfeld; optionale Felder wie bisher im Board leer = None.
            setattr(value, name, gekuerzt if name == "title" else gekuerzt or None)
    if felder.get("marker_type") in CROSS_MARKER_TYPE_LABEL:
        value.marker_type = felder["marker_type"]
    if felder.get("status") in CROSS_MARKER_STATUS_LABEL:
        value.status = felder["status"]
    for name in ("lat", "lng"):
        if name in felder:
            setattr(value, name, felder[name])
    return value


def log_eintrag(db: Session, lage: MajorIncident, marker_id: int, text: str,
                user_id: int | None, author_name: str | None) -> CrossMarkerLogEntry:
    marker(lage, db, marker_id)
    entry = CrossMarkerLogEntry(marker_id=marker_id, text=text.strip()[:500], user_id=user_id, author_name=author_name)
    db.add(entry)
    return entry
