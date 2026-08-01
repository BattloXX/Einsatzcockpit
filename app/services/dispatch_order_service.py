"""Gemeinsame Aufloesung der passenden Ausrueckordnungs-Variante."""

from sqlalchemy.orm import Session

from app.models.master import AlarmDispatchVehicle


def resolve_dispatch_is_ausserorts(
    incident_address_city: str | None,
    org_city: str | None,
) -> bool:
    """True, wenn die Einsatzadresse ausserhalb des Organisationsorts liegt."""
    if not incident_address_city or not org_city:
        return False
    return incident_address_city.strip().casefold() != org_city.strip().casefold()


def resolve_dispatch_entries(
    db: Session,
    alarm_type_id: int,
    is_ausserorts: bool,
) -> list[AlarmDispatchVehicle]:
    """Passende AAO laden; Ausserorts faellt bei Bedarf auf Standard zurueck."""
    query = (
        db.query(AlarmDispatchVehicle)
        .filter(AlarmDispatchVehicle.alarm_type_id == alarm_type_id)
        .execution_options(include_all_tenants=True)
    )
    if is_ausserorts:
        entries = (
            query.filter(AlarmDispatchVehicle.is_ausserorts.is_(True))
            .order_by(AlarmDispatchVehicle.display_order)
            .all()
        )
        if entries:
            return entries
    return (
        query.filter(AlarmDispatchVehicle.is_ausserorts.is_(False))
        .order_by(AlarmDispatchVehicle.display_order)
        .all()
    )
