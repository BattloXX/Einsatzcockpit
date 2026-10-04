"""Zentrale Revisionsnummern fuer den read-only Einsatz-Feed."""
from __future__ import annotations

from sqlalchemy import event, inspect, update
from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentColumn, IncidentVehicle, IncidentWacheStatus, Message, Task
from app.models.objekt import ObjektEinsatz

# VehicleMaster-/Stammdaten-Umbenennungen bumpen bewusst nicht; sie haben keine incident_id.
_CHILD_TYPES = (IncidentColumn, IncidentVehicle, IncidentWacheStatus, Task, Message, ObjektEinsatz)


def _incident_ids(session: Session) -> set[int]:
    """Ermittelt alle bestehenden Einsaetze, deren Feed-Snapshot sich aenderte."""
    ids: set[int] = set()
    deleted_incident_ids = {
        obj.id for obj in session.deleted if isinstance(obj, Incident) and obj.id is not None
    }

    for obj in session.new:
        # Ein gerade angelegter Einsatz beginnt bewusst mit der Server-Vorgabe 0.
        if isinstance(obj, _CHILD_TYPES):
            incident_id = obj.incident_id
            if incident_id is not None:
                ids.add(incident_id)

    for obj in session.dirty:
        if isinstance(obj, Incident):
            state = inspect(obj)
            if not session.is_modified(obj, include_collections=False):
                continue
            changed = [
                attribute.key for attribute in state.attrs
                if attribute.history.has_changes()
            ]
            if changed and set(changed) != {"feed_rev"} and obj.id is not None:
                ids.add(obj.id)
        elif isinstance(obj, _CHILD_TYPES) and session.is_modified(obj, include_collections=False):
            incident_id = obj.incident_id
            if incident_id is not None:
                ids.add(incident_id)

    for obj in session.deleted:
        if isinstance(obj, _CHILD_TYPES):
            incident_id = obj.incident_id
            if incident_id is not None and incident_id not in deleted_incident_ids:
                ids.add(incident_id)

    # Kinder eines in diesem Flush neu angelegten Einsatzes duerfen dessen Startwert
    # nicht sofort von 0 auf 1 setzen.
    ids.difference_update(
        obj.id for obj in session.new if isinstance(obj, Incident) and obj.id is not None
    )
    ids.difference_update(deleted_incident_ids)
    return ids


def _bump_feed_rev(session: Session, _flush_context) -> None:
    incident_ids = _incident_ids(session)
    if not incident_ids:
        return

    session.connection().execute(
        update(Incident)
        .where(Incident.id.in_(incident_ids))
        .values(feed_rev=Incident.feed_rev + 1)
    )
    for obj in session.identity_map.values():
        if isinstance(obj, Incident) and obj.id in incident_ids:
            session.expire(obj, ["feed_rev"])


def register_feed_rev_listener() -> None:
    """Registriert den Hook einmalig auf der globalen SQLAlchemy-Session."""
    event.listen(Session, "after_flush", _bump_feed_rev)
