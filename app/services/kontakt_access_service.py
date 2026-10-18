"""Kontaktanzeige im Einsatz: Einsatzrecht, Zuordnung und sparsame Feldausgabe."""

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.core.permissions import can_access_incident, can_view_kontakt_directory_request
from app.models.incident import Incident, IncidentVehicle
from app.models.kontakt import Kontakt
from app.models.objekt import Objekt, ObjektEinsatz
from app.models.user import DeviceToken


def incident_for_contacts(db: Session, request: Request, incident_id: int) -> Incident:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    qr_incident = getattr(request.state, "qr_incident_id", None)
    if (
        incident is None
        or not can_access_incident(user, incident)
        or (qr_incident is not None and qr_incident != incident.id)
        or getattr(request.state, "qr_lage_id", None) is not None
    ):
        raise HTTPException(404, "Einsatz nicht gefunden")
    if getattr(user, "is_device", False) or getattr(request.state, "is_device", False):
        # Ausschliesslich das authentifizierte Geraet, nie irgendein Token
        # desselben Benutzers. Alte Sessions ohne Tokenbindung bleiben gesperrt.
        token_id = getattr(request.state, "device_token_id", None)
        token = (
            db.query(DeviceToken)
            .filter(
                DeviceToken.id == token_id,
                DeviceToken.user_id == user.id,
                DeviceToken.revoked_at.is_(None),
            )
            .first()
            if token_id is not None
            else None
        )
        assigned = (
            token
            and token.vehicle_master_id
            and db.query(IncidentVehicle.id)
            .filter(
                IncidentVehicle.incident_id == incident.id,
                IncidentVehicle.vehicle_master_id == token.vehicle_master_id,
                IncidentVehicle.removed_at.is_(None),
            )
            .first()
        )
        if not assigned:
            raise HTTPException(404, "Einsatz nicht gefunden")
    return incident


def objekt_contacts_visible(db: Session, request: Request, objekt: Objekt) -> bool:
    if can_view_kontakt_directory_request(request):
        return True
    raw_incident = request.query_params.get("incident_id")
    if not raw_incident or not raw_incident.isdigit():
        return False
    incident = incident_for_contacts(db, request, int(raw_incident))
    return bool(
        db.query(ObjektEinsatz.id)
        .filter(
            ObjektEinsatz.incident_id == incident.id,
            ObjektEinsatz.objekt_id == objekt.id,
            ObjektEinsatz.org_id == objekt.org_id,
            ObjektEinsatz.status == "bestaetigt",
        )
        .first()
    )


def contact_summary(kontakt: Kontakt, *, full: bool = False) -> dict:
    """Keine internen Notizen, Adressen, Quellen oder fremden Zuordnungen."""
    return {
        "id": kontakt.id,
        "anzeigename": kontakt.anzeigename,
        "funktion": kontakt.funktion,
        "organisation": kontakt.organisation,
        "telefone": [
            {"nummer": tel.nummer, "label": tel.label}
            for tel in kontakt.telefone
            if full or (tel.aktiv and tel.verwendung == "dienst")
        ],
    }


def objekt_contact_summaries(db: Session, request: Request, objekt: Objekt) -> list[dict]:
    if not objekt_contacts_visible(db, request, objekt):
        return []
    full = can_view_kontakt_directory_request(request)
    return [
        contact_summary(k.zentraler_kontakt, full=full) | {"art": k.art, "erreichbarkeit": k.erreichbarkeit}
        for k in objekt.kontakte
        if k.zentraler_kontakt
        and (full or (k.einsatz_sichtbar and k.zentraler_kontakt.aktiv and not k.zentraler_kontakt.archiviert))
    ]
