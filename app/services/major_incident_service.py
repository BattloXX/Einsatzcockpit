"""Business-Logic für Großschadenslagen (MajorIncident).

Regeln:
- Genau eine aktive Lage je Org (status=active). Weitere werden als standby angelegt.
- Schließen ausschließlich manuell über close_lage() — kein Auto-Close.
- Auslösung automatisch via triggers_major_incident-Flag am Alarmtyp
  oder manuell über create_lage().
- Auto-Adopt: Wenn OrgSettings.mi_auto_adopt und eine aktive Lage läuft,
  wird jeder eingehende Einsatz als IncidentSite gespiegelt.
"""
import secrets
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.major_incident import (
    IncidentSite,
    MajorIncident,
    MajorIncidentStatus,
    SitePhase,
)


def get_active_lage(db: Session, org_id: int) -> MajorIncident | None:
    return (
        db.query(MajorIncident)
        .filter(
            MajorIncident.org_id == org_id,
            MajorIncident.status == MajorIncidentStatus.active,
        )
        .first()
    )


def create_lage(
    db: Session,
    org_id: int,
    name: str,
    *,
    trigger: str = "manual",
    is_exercise: bool = False,
    started_by_user_id: int | None = None,
    description: str | None = None,
) -> MajorIncident:
    """Erstellt eine neue Lage mit status=active und einem öffentlichen Token."""
    lage = MajorIncident(
        org_id=org_id,
        name=name,
        description=description,
        status=MajorIncidentStatus.active,
        trigger=trigger,
        is_exercise=is_exercise,
        started_by_user_id=started_by_user_id,
        public_token=secrets.token_urlsafe(24),
    )
    db.add(lage)
    db.flush()
    return lage


def close_lage(
    db: Session,
    lage: MajorIncident,
    *,
    closed_by_user_id: int | None = None,
) -> MajorIncident:
    """Schließt die Lage manuell. Erlischt den Bürger-Token."""
    lage.status = MajorIncidentStatus.closed
    lage.ended_at = datetime.now(UTC)
    lage.public_token = None            # Token erlischt beim Schließen
    lage.public_token_expires_at = None
    return lage


def rotate_public_token(db: Session, lage: MajorIncident) -> MajorIncident:
    """Generiert einen neuen Bürger-Token (alter Link wird ungültig)."""
    lage.public_token = secrets.token_urlsafe(24)
    lage.public_token_expires_at = None
    return lage


def create_site(
    db: Session,
    lage: MajorIncident,
    bezeichnung: str,
    *,
    org_id: int | None = None,
    einsatzgrund: str | None = None,
    ort: str | None = None,
    strasse: str | None = None,
    hausnr: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    source: str = "manual",
    external_key: str | None = None,
    alarm_stufe: str | None = None,
    incident_id: int | None = None,
    created_by: int | None = None,
) -> IncidentSite:
    site = IncidentSite(
        major_incident_id=lage.id,
        org_id=org_id or lage.org_id,
        bezeichnung=bezeichnung,
        einsatzgrund=einsatzgrund,
        ort=ort,
        strasse=strasse,
        hausnr=hausnr,
        lat=lat,
        lng=lng,
        source=source,
        external_key=external_key,
        alarm_stufe=alarm_stufe,
        incident_id=incident_id,
        created_by=created_by,
        phase=SitePhase.eingegangen,
    )
    db.add(site)
    db.flush()
    return site


def handle_alarm_trigger(
    db: Session,
    org_id: int,
    alarm_type_code: str,
    incident_id: int,
    external_key: str,
    *,
    is_exercise: bool = False,
    bezeichnung: str | None = None,
    ort: str | None = None,
    strasse: str | None = None,
    hausnr: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    einsatzgrund: str | None = None,
) -> tuple[MajorIncident, IncidentSite, bool]:
    """Verarbeitet einen eingehenden Alarm auf Großschadenslage-Relevanz.

    Gibt (lage, site, lage_created) zurück.
    Falls eine identische external_key-Site bereits existiert → idempotent.
    """
    lage = get_active_lage(db, org_id)
    lage_created = False

    if lage is None:
        name = f"Lage {alarm_type_code} – {ort or 'unbekannt'} {datetime.now(UTC).strftime('%d.%m.%Y %H:%M')}"
        lage = create_lage(
            db, org_id, name,
            trigger="alarm_auto",
            is_exercise=is_exercise,
        )
        lage_created = True

    # Idempotenz: externe Key bereits als Site erfasst?
    existing_site = (
        db.query(IncidentSite)
        .filter(
            IncidentSite.major_incident_id == lage.id,
            IncidentSite.external_key == external_key,
        )
        .first()
    )
    if existing_site:
        return lage, existing_site, lage_created

    site = create_site(
        db, lage,
        bezeichnung=bezeichnung or einsatzgrund or f"{alarm_type_code} – {ort or 'unbekannt'}",
        source="api",
        external_key=external_key,
        alarm_stufe=alarm_type_code,
        incident_id=incident_id,
        org_id=org_id,
        ort=ort,
        strasse=strasse,
        hausnr=hausnr,
        lat=lat,
        lng=lng,
        einsatzgrund=einsatzgrund,
    )
    return lage, site, lage_created


def adopt_incident_as_site(
    db: Session,
    lage: MajorIncident,
    *,
    incident_id: int,
    external_key: str,
    alarm_type_code: str,
    org_id: int,
    ort: str | None = None,
    strasse: str | None = None,
    hausnr: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    einsatzgrund: str | None = None,
) -> IncidentSite | None:
    """Spiegelt einen normalen Einsatz als Einsatzstelle, wenn mi_auto_adopt aktiv.

    Gibt None zurück, wenn die Site bereits existiert (idempotent).
    """
    existing = (
        db.query(IncidentSite)
        .filter(
            IncidentSite.major_incident_id == lage.id,
            IncidentSite.incident_id == incident_id,
        )
        .first()
    )
    if existing:
        return None

    return create_site(
        db, lage,
        bezeichnung=einsatzgrund or f"{alarm_type_code} – {ort or 'unbekannt'}",
        source="api",
        external_key=external_key,
        alarm_stufe=alarm_type_code,
        incident_id=incident_id,
        org_id=org_id,
        ort=ort,
        strasse=strasse,
        hausnr=hausnr,
        lat=lat,
        lng=lng,
        einsatzgrund=einsatzgrund,
    )
