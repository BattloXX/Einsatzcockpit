"""Read-only, privacy-safe serialization for MCP incident tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.core.permissions import STRASSENSPERREN_LESE_ROLLEN
from app.models.incident import Incident, IncidentOrg, Message, MessageMedia, Task, TaskMedia
from app.models.objekt import Objekt, ObjektBMA

EINSATZ_LESE_ROLLEN = STRASSENSPERREN_LESE_ROLLEN

# Die Personenkarte schreibt "person" (siehe incident/_person_modal.html). Die
# weiteren Werte decken alte sowie modellnahe Einträge defensiv ab.
_PERSONEN_ENTITY_TYPES = {
    "person",
    "rescued_person",
    "rescuedperson",
    "breathing_troop",
    "breathingtroop",
    "atemschutz",
    "atemschutz_pruefung",
}


def json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.isoformat() + "Z"
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def ui_link(incident_id: int) -> str:
    return f"{settings.effective_public_base_url.rstrip('/')}/einsatz/{incident_id}"


def sichtbarer_einsatz(db: Any, org_id: int, incident_id: int) -> Incident:
    incident = db.query(Incident).execution_options(include_all_tenants=True).filter(Incident.id == incident_id).first()
    member = db.query(IncidentOrg).filter(IncidentOrg.incident_id == incident_id, IncidentOrg.org_id == org_id).first()
    if incident is None or (incident.primary_org_id != org_id and member is None):
        raise ValueError("Einsatz nicht gefunden.")
    return incident


def medium_dict(medium: TaskMedia | MessageMedia) -> dict[str, Any]:
    return json_value(
        {
            "id": medium.id,
            "kind": medium.kind,
            "dateiname": medium.original_filename,
            "mime": medium.mime_type,
            "bytes": medium.bytes,
            "pages": medium.pages,
            "created_at": medium.created_at,
        }
    )


def task_dict(task: Task) -> dict[str, Any]:
    return json_value(
        {
            "id": task.id,
            "titel": task.title,
            "detail": task.detail,
            "quelle": task.source,
            "status": task.status,
            "erledigt": task.is_done,
            "erledigt_am": task.done_at,
            "storniert": task.is_cancelled,
            "faellig_am": task.due_at,
            "ueberfaellig": bool(
                task.due_at
                and not task.is_done
                and not task.is_cancelled
                and task.due_at < datetime.now(UTC).replace(tzinfo=None)
            ),
            "einheit": None
            if task.vehicle is None
            else {"id": task.vehicle.id, "name": task.vehicle.master.name if task.vehicle.master else None},
            "abschnitt": None if task.column is None else {"id": task.column.id, "titel": task.column.title},
            "erstellt": task.created_at,
            "medien": [medium_dict(medium) for medium in task.media],
        }
    )


def message_dict(message: Message) -> dict[str, Any]:
    return json_value(
        {
            "id": message.id,
            "titel": message.title,
            "detail": message.detail,
            "status": message.status,
            "erledigt": message.is_done,
            "erledigt_am": message.done_at,
            "storniert": message.is_cancelled,
            "faellig_am": message.due_at,
            "ueberfaellig": bool(
                message.due_at
                and not message.is_done
                and not message.is_cancelled
                and message.due_at < datetime.now(UTC).replace(tzinfo=None)
            ),
            "einheit": None
            if message.vehicle is None
            else {"id": message.vehicle.id, "name": message.vehicle.master.name if message.vehicle.master else None},
            "abschnitt": None if message.column is None else {"id": message.column.id, "titel": message.column.title},
            "erstellt": message.created_at,
            "objekt_gefahr": None
            if message.objekt_gefahr is None
            else {
                "id": message.objekt_gefahr.id,
                "name": message.objekt_gefahr.stoffname or message.objekt_gefahr.gefahr.name,
            },
            "medien": [medium_dict(medium) for medium in message.media],
        }
    )


def objekt_einsatz_payload(db: Any, objekt: Objekt) -> dict[str, Any]:
    """Serialisiert Objekt-Einsatzdaten ohne Kontakt- oder Verwaltungsdaten."""
    del db
    return {
        "id": objekt.id,
        "anzeige_nummer": objekt.anzeige_nummer,
        "name": objekt.name,
        "adresse_zeile": objekt.adresse_zeile,
        "status": objekt.status,
        "vulgoname": objekt.vulgoname,
        "kategorie_id": objekt.kategorie_id,
        "kategorie": objekt.kategorie.name if objekt.kategorie else None,
        "lat": objekt.lat,
        "lng": objekt.lng,
        "informationen": objekt.informationen,
        "anfahrtsweg": objekt.anfahrtsweg,
        "revision_datum": objekt.revision_datum.isoformat() if objekt.revision_datum else None,
        "adresse": {
            "strasse": objekt.strasse,
            "hausnummer": objekt.hausnummer,
            "plz": objekt.plz,
            "ort": objekt.ort,
        },
        "bma": (
            {
                column.name: getattr(objekt.bma, column.name)
                for column in ObjektBMA.__table__.columns
                if column.name not in {"id", "org_id", "objekt_id", "benachrichtigung_sms", "benachrichtigung_email"}
            }
            | {
                "benachrichtigung_sms_gesetzt": bool(objekt.bma.benachrichtigung_sms),
                "benachrichtigung_email_gesetzt": bool(objekt.bma.benachrichtigung_email),
            }
            if objekt.bma
            else None
        ),
        "gefahren": [
            {
                "id": gefahr.id,
                "gefahr_id": gefahr.gefahr_id,
                "name": gefahr.gefahr.name if gefahr.gefahr else None,
                "un_nummer": gefahr.un_nummer,
                "stoffname": gefahr.stoffname,
                "gefahrklasse": gefahr.gefahrklasse,
                "gefahrnummer": gefahr.gefahrnummer,
                "detail": gefahr.detail,
                "links": gefahr.links,
                "sort": gefahr.sort,
            }
            for gefahr in objekt.gefahren
        ],
        "merkmale": [
            {
                "id": merkmal.id,
                "merkmal_id": merkmal.merkmal_id,
                "name": merkmal.merkmal.name if merkmal.merkmal else None,
                "hinweis": merkmal.hinweis,
            }
            for merkmal in objekt.merkmale
        ],
        "zusatzadressen": [
            {
                "id": adresse.id,
                "bezeichnung": adresse.bezeichnung,
                "strasse": adresse.strasse,
                "hausnummer": adresse.hausnummer,
                "plz": adresse.plz,
                "ort": adresse.ort,
                "sort": adresse.sort,
            }
            for adresse in objekt.zusatzadressen
        ],
        "wohnanlage": (
            {
                "wohneinheiten": objekt.wohnanlage.wohneinheiten,
                "geschosse": objekt.wohnanlage.geschosse,
                "stiegen": objekt.wohnanlage.stiegen,
                "hinweise": objekt.wohnanlage.hinweise,
            }
            if objekt.wohnanlage
            else None
        ),
        "kontakte_anzahl": len(objekt.kontakte),
    }
