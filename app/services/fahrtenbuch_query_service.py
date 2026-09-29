"""Gemeinsame, rein lesende Abfragen für das Fahrtenbuch."""

from sqlalchemy.orm import Session, joinedload

from app.core.timezones import local_date_to_utc
from app.models.fahrtenbuch import Fahrt, FahrtKategorie, FahrtStatus


def gefilterte_fahrten_query(
    db: Session,
    org_id: int,
    org: object,
    *,
    von: str = "",
    bis: str = "",
    fahrzeug_id: int = 0,
    fahrttyp: str = "",
    zweck_id: int = 0,
    status: str = "aktiv",
    nur_statistikrelevant: bool = False,
    mit_incident: bool = False,
):
    """Erstellt die bisher von Verwaltungsliste und Export geteilte Filterabfrage."""
    optionen = [joinedload(Fahrt.fahrzeug), joinedload(Fahrt.zweck), joinedload(Fahrt.zielort)]
    if mit_incident:
        optionen.append(joinedload(Fahrt.incident))
    query = (
        db.query(Fahrt)
        .filter(Fahrt.org_id == org_id)
        .execution_options(include_all_tenants=True)
        .options(*optionen)
    )
    if status and status != "alle":
        try:
            query = query.filter(Fahrt.status == FahrtStatus(status))
        except ValueError:
            pass
    zeitpunkt = local_date_to_utc(von, org=org) if von else None
    if zeitpunkt:
        query = query.filter(Fahrt.zeitpunkt >= zeitpunkt)
    zeitpunkt = local_date_to_utc(bis, end=True, org=org) if bis else None
    if zeitpunkt:
        query = query.filter(Fahrt.zeitpunkt <= zeitpunkt)
    if fahrzeug_id:
        query = query.filter(Fahrt.fahrzeug_id == fahrzeug_id)
    if fahrttyp:
        try:
            query = query.filter(Fahrt.fahrttyp == FahrtKategorie(fahrttyp))
        except ValueError:
            pass
    if zweck_id:
        query = query.filter(Fahrt.zweck_id == zweck_id)
    if nur_statistikrelevant:
        query = query.filter(Fahrt.nicht_statistikrelevant == False)  # noqa: E712
    return query
