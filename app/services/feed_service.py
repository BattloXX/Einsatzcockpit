"""Aufbereitung der explizit erlaubten Einsatz-Feed-Daten."""
from __future__ import annotations

from datetime import UTC, datetime

from app.config import settings
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT
from app.schemas.feed import (
    FeedBoard,
    FeedBoardColumn,
    FeedBoardMessage,
    FeedBoardTask,
    FeedEinsatzBasis,
    FeedKraft,
    FeedObjektBasis,
    FeedWache,
)


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _phase(incident: Incident) -> str:
    statuses = [
        vehicle.unit_status for vehicle in incident.vehicles
        if vehicle.removed_at is None
    ]
    if not statuses:
        return "alarmiert"
    if all(status == "Einsatzbereit" for status in statuses):
        return "abschluss"
    if any(status == "Am Einsatzort" for status in statuses):
        return "einsatzstelle"
    return "anfahrt"


def _confirmed_objekt(incident: Incident) -> FeedObjektBasis | None:
    links = sorted(incident.objekt_links, key=lambda link: link.erstellt_am)
    for link in links:
        if link.status == OBJEKT_EINSATZ_BESTAETIGT and link.objekt is not None:
            return FeedObjektBasis(id=link.objekt.id, name=link.objekt.name)
    return None


def _kraefte(incident: Incident) -> list[FeedKraft]:
    return [
        FeedKraft(
            id=vehicle.id,
            vehicle_code=vehicle.vehicle_master.code,
            name=vehicle.vehicle_master.name,
            type=vehicle.vehicle_master.type,
            is_external=vehicle.vehicle_master.is_external,
            org_short=vehicle.vehicle_master.adhoc_org_short,
            unit_status=vehicle.unit_status,
            added_at=_utc_iso(vehicle.created_at) or "",
        )
        for vehicle in incident.vehicles if vehicle.removed_at is None
    ]


def _wachen(incident: Incident) -> list[FeedWache]:
    return [
        FeedWache(
            wache_unid=wache.wache_unid,
            wache_name=wache.wache_name,
            status=wache.status,
            status_at=_utc_iso(wache.status_at),
        )
        for wache in sorted(incident.wache_status_entries, key=lambda item: item.wache_unid)
    ]


def _board(incident: Incident) -> FeedBoard:
    column_codes = {column.id: column.code for column in incident.columns}
    return FeedBoard(
        columns=[
            FeedBoardColumn(code=column.code, title=column.title,
                            column_kind=column.column_kind, display_order=column.display_order)
            for column in incident.columns
        ],
        tasks=[
            FeedBoardTask(
                id=task.id, title=task.title, status=task.status, is_done=task.is_done,
                done_at=_utc_iso(task.done_at), is_cancelled=task.is_cancelled,
                cancelled_at=_utc_iso(task.cancelled_at), due_at=_utc_iso(task.due_at),
                created_at=_utc_iso(task.created_at) or "",
                column_code=(column_codes.get(task.column_id) if task.column_id is not None else None),
                vehicle_id=task.vehicle_id,
            )
            for task in incident.tasks
        ],
        messages=[
            FeedBoardMessage(
                id=message.id, title=message.title, status=message.status, is_done=message.is_done,
                done_at=_utc_iso(message.done_at), is_cancelled=message.is_cancelled,
                created_at=_utc_iso(message.created_at) or "",
                column_code=(
                    column_codes.get(message.column_id) if message.column_id is not None else None
                ),
                vehicle_id=message.vehicle_id,
            )
            for message in incident.messages
        ],
    )


def build_feed_einsatz_basis(
    incident: Incident, org: FireDept, include: frozenset[str] = frozenset(),
) -> FeedEinsatzBasis:
    """Baut einen Basis-DTO ohne ORM-Dump und ohne personenbezogene Daten."""
    started_at = _utc_iso(incident.started_at)
    assert started_at is not None
    return FeedEinsatzBasis(
        id=incident.id,
        nummer=incident.nummer,
        alarm_type_code=incident.alarm_type_code,
        status=incident.status,
        is_exercise=incident.is_exercise,
        phase=_phase(incident),
        started_at=started_at,
        closed_at=_utc_iso(incident.closed_at),
        taken_over_at=_utc_iso(incident.taken_over_at),
        departed_at=_utc_iso(incident.departed_at),
        on_scene_at=_utc_iso(incident.on_scene_at),
        ready_again_at=_utc_iso(incident.ready_again_at),
        address_street=incident.address_street,
        address_no=incident.address_no,
        address_city=incident.address_city,
        lat=incident.lat,
        lng=incident.lng,
        objekt=_confirmed_objekt(incident),
        kraefte=_kraefte(incident) if "kraefte" in include else None,
        wachen=_wachen(incident) if "wachen" in include else None,
        board=_board(incident) if "board" in include else None,
        unit_count=sum(1 for vehicle in incident.vehicles if vehicle.removed_at is None),
        timezone=org.timezone or settings.DEFAULT_TIMEZONE,
    )
