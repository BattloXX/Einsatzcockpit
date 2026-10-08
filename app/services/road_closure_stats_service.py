"""Kennzahlen fuer sichtbare Strassensperren."""

from datetime import UTC, datetime, timedelta

from app.core.timezones import local_date_to_utc, org_tz
from app.services import road_closure_service


def kennzahlen(db, org, *, scope: str = "all", now: datetime | None = None) -> dict:
    """Return current closure metrics without bypassing visibility rules."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    visible_scope = "all" if scope == "all" else "own"
    current = road_closure_service.list_closures(db, org.id, status="current", scope=visible_scope, now=now)
    local_today = now.replace(tzinfo=UTC).astimezone(org_tz(org)).date()
    today_start = local_date_to_utc(local_today.isoformat(), org=org)
    today_end = local_date_to_utc(local_today.isoformat(), org=org, end=True)
    assert today_start is not None and today_end is not None
    week_end = today_end + timedelta(days=7)
    states = {closure.id: road_closure_service.compute_status(closure, now) for closure in current}
    active = [closure for closure in current if states[closure.id] == "active"]
    planned = [closure for closure in current if states[closure.id] == "planned"]
    by_type: dict[str, int] = {}
    for closure in current:
        by_type[closure.restriction_type] = by_type.get(closure.restriction_type, 0) + 1
    upcoming = [
        value
        for closure in current
        for value in (closure.valid_from, closure.valid_until)
        if value is not None and value > now
    ]
    return {
        "aktiv": len(active),
        "geplant": len(planned),
        "vollsperren_aktiv": sum(closure.restriction_type == "closed" for closure in active),
        "beginnt_heute": sum(
            states[closure.id] in {"planned", "active"} and today_start <= closure.valid_from <= today_end
            for closure in current
        ),
        "endet_in_7_tagen": sum(
            closure.valid_until is not None and now <= closure.valid_until <= week_end for closure in active
        ),
        "geometrie_pruefen": sum(
            closure.org_id == org.id and closure.geometry_status == "needs_review" for closure in current
        ) if scope == "all" else 0,
        "nach_typ": by_type,
        "naechste_aenderung": min(upcoming).isoformat() if upcoming else None,
    }
