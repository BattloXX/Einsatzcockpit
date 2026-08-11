from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.timezones import local_date_to_utc, now_local, to_org_tz
from app.models.incident import Incident, IncidentOrg, IncidentVehicle
from app.models.master import AlarmType, FireDept, VehicleMaster


@dataclass
class StatsResult:
    total: int
    total_exercises: int
    fire_count: int
    technical_count: int
    other_count: int
    avg_duration_min: float | None
    avg_time_to_first_vehicle_min: float | None
    by_month: list[dict[str, Any]]
    by_alarm_type: list[dict[str, Any]]
    vehicle_usage: list[dict[str, Any]]
    vehicle_fleet_stats: list[dict[str, Any]]
    recent_incidents: list[Incident]
    incidents: list[Incident]
    map_markers: list[dict[str, Any]]


def default_range(org: FireDept | None) -> tuple[date, date]:
    today = now_local(org).date()
    return date(today.year, 1, 1), today


def _incident_scope(q, db: Session, org_id: int, user=None):
    if user is not None:
        # Deliberately imported here to retain ui_stats._apply_org_scope as the
        # single source of truth and its established import path.
        from app.routers.ui_stats import _apply_org_scope
        return _apply_org_scope(q, user, db)
    collab = db.query(IncidentOrg.incident_id).filter(IncidentOrg.org_id == org_id)
    return q.filter(or_(Incident.primary_org_id == org_id, Incident.id.in_(collab)))


def get_stats(
    db: Session, org_id: int, von: date, bis: date, user=None,
) -> StatsResult:
    org = db.query(FireDept).filter(FireDept.id == org_id).first()
    start = local_date_to_utc(von.isoformat(), org=org)
    end = local_date_to_utc(bis.isoformat(), end=True, org=org)

    q = db.query(Incident).filter(Incident.started_at >= start, Incident.started_at <= end)
    incidents_all = _incident_scope(q, db, org_id, user).order_by(Incident.started_at.desc()).all()
    exercises = [i for i in incidents_all if i.is_exercise]
    incidents = [i for i in incidents_all if not i.is_exercise]

    alarm_rows = (
        db.query(AlarmType)
        .execution_options(include_all_tenants=True)
        .filter(AlarmType.org_id == org_id)
        .all()
    )
    alarm_by_code = {a.code: a for a in alarm_rows}
    alarm_counts: dict[str, int] = {}
    categories = {"fire": 0, "technical": 0, "other": 0}
    durations: list[float] = []
    months: dict[str, int] = {}
    markers: list[dict[str, Any]] = []
    for incident in incidents:
        alarm_counts[incident.alarm_type_code] = alarm_counts.get(incident.alarm_type_code, 0) + 1
        category = (alarm_by_code.get(incident.alarm_type_code).category
                    if alarm_by_code.get(incident.alarm_type_code) else "")
        if category in ("B", "F"):
            categories["fire"] += 1
        elif category == "T":
            categories["technical"] += 1
        else:
            categories["other"] += 1
        month = to_org_tz(incident.started_at, org).strftime("%Y-%m")
        months[month] = months.get(month, 0) + 1
        if incident.closed_at and incident.closed_at >= incident.started_at:
            durations.append((incident.closed_at - incident.started_at).total_seconds() / 60)
        if incident.lat is not None and incident.lng is not None:
            address = " ".join(filter(None, [incident.address_street, incident.address_no, incident.address_city]))
            markers.append({
                "id": incident.id, "lat": incident.lat, "lng": incident.lng,
                "alarm_type_code": incident.alarm_type_code,
                "category": category, "nummer": incident.nummer, "address": address,
            })

    by_alarm_type = []
    for code, count in sorted(alarm_counts.items(), key=lambda item: (-item[1], item[0])):
        alarm = alarm_by_code.get(code)
        by_alarm_type.append({
            "code": code, "label": alarm.label if alarm else "", "count": count,
            "percent": round(count * 100 / len(incidents), 1) if incidents else 0,
        })

    incident_ids = [i.id for i in incidents]
    vehicle_rows = []
    if incident_ids:
        vehicle_rows = (
            db.query(IncidentVehicle, VehicleMaster)
            .join(VehicleMaster, VehicleMaster.id == IncidentVehicle.vehicle_master_id)
            .filter(
                IncidentVehicle.incident_id.in_(incident_ids),
                VehicleMaster.dept_id == org_id,
            )
            .all()
        )
    usage: dict[int, dict[str, Any]] = {}
    first_dispatch: dict[int, Any] = {}
    incident_by_id = {i.id: i for i in incidents}
    for assignment, vehicle in vehicle_rows:
        # The incident scope above is the authorization boundary; vehicle rows
        # are limited to exactly those authorized incident IDs.
        item = usage.setdefault(vehicle.id, {
            "id": vehicle.id, "code": vehicle.code, "name": vehicle.name,
            "count": 0, "km": 0,
        })
        item["count"] += 1
        item["km"] += assignment.km_gefahren or 0
        previous = first_dispatch.get(assignment.incident_id)
        if previous is None or assignment.created_at < previous:
            first_dispatch[assignment.incident_id] = assignment.created_at
    reaction = []
    for incident_id, dispatched_at in first_dispatch.items():
        started_at = incident_by_id[incident_id].started_at
        if dispatched_at >= started_at:
            reaction.append((dispatched_at - started_at).total_seconds() / 60)

    fleet = (
        db.query(VehicleMaster)
        .filter(
            VehicleMaster.dept_id == org_id,
            VehicleMaster.active == True,  # noqa: E712
            VehicleMaster.deleted == False,  # noqa: E712
        )
        .execution_options(include_all_tenants=True)
        .order_by(VehicleMaster.display_order, VehicleMaster.code)
        .all()
    )
    fleet_stats = [{
        "id": v.id, "code": v.code, "name": v.name,
        "km_aktuell": v.km_aktuell, "betriebsstunden_aktuell": float(v.betriebsstunden_aktuell or 0),
    } for v in fleet]

    return StatsResult(
        total=len(incidents), total_exercises=len(exercises),
        fire_count=categories["fire"], technical_count=categories["technical"],
        other_count=categories["other"],
        # Lifecycle auto-closes and retroactively assigned vehicles preserve valid but
        # potentially days-late timestamps. The median keeps those records in the
        # statistics without allowing that outlier class to dominate either KPI.
        avg_duration_min=round(median(durations), 1) if durations else None,
        avg_time_to_first_vehicle_min=round(median(reaction), 1) if reaction else None,
        by_month=[{"month": key, "count": months[key]} for key in sorted(months)],
        by_alarm_type=by_alarm_type,
        vehicle_usage=sorted(usage.values(), key=lambda item: (-item["count"], item["code"])),
        vehicle_fleet_stats=fleet_stats, recent_incidents=incidents[:15], incidents=incidents,
        map_markers=markers,
    )
