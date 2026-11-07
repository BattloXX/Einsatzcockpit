"""Reiner Resolver und UTC-Timeline; keine Datenbank, keine Systemuhr.

Lokale Luecken werden vorwaerts verschoben; doppelte Uhrzeiten beginnen beim
ersten und enden beim zweiten Auftreten. Intervalle sind halboffen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.infoscreen_aktivitaet import Aktivitaet


@dataclass(frozen=True)
class Display:
    id: int
    org_id: int
    url: str
    locked: bool = False
    gsl_override_erlaubt: bool = False
    nachlauf_min: int = 10
    config_version: int = 1
    revision: int = 0


@dataclass(frozen=True)
class Schedule:
    timezone: str = "Europe/Vienna"
    slots: list[dict[str, Any]] = field(default_factory=list)
    exceptions: dict[date, list[dict[str, Any]]] = field(default_factory=dict)


def utc(value: datetime) -> datetime:
    """DB-Zeit ist naive UTC; aware Eingaben auf UTC normieren."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def iso(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def _local(day: date, value: time | str, zone: ZoneInfo, *, end: bool = False) -> datetime:
    clock = time.fromisoformat(value) if isinstance(value, str) else value
    naive = datetime.combine(day, clock)
    candidates = [naive.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)]
    real = [dt for dt in candidates if dt.astimezone(zone).replace(tzinfo=None) == naive]
    return (max(real) if end else min(real)) if real else max(candidates)


def _intervals(schedule: Schedule, start: datetime, end: datetime) -> list[tuple]:
    zone = ZoneInfo(schedule.timezone)
    day = start.astimezone(zone).date() - timedelta(days=1)
    result = []
    while day <= end.astimezone(zone).date():
        exception = day in schedule.exceptions
        slots = schedule.exceptions[day] if exception else schedule.slots
        day_start = _local(day, time.min, zone)
        day_end = _local(day + timedelta(days=1), time.min, zone, end=True)
        if exception:
            # Auch Luecken zwischen Ersatz-Slots sind am Ausnahmetag aus.
            result.append((day_start, day_end, (1, float("-inf"), datetime.combine(day, time.min), -1), "off", None))
        for index, slot in enumerate(slots):
            if not exception and not (slot["weekdays"] & (1 << day.weekday())):
                continue
            valid_from, valid_to = slot.get("valid_from"), slot.get("valid_to")
            if isinstance(valid_from, str):
                valid_from = date.fromisoformat(valid_from)
            if isinstance(valid_to, str):
                valid_to = date.fromisoformat(valid_to)
            if (valid_from and day < valid_from) or (valid_to and day > valid_to):
                continue
            begin = _local(day, slot["start_time"], zone)
            a = time.fromisoformat(slot["start_time"]) if isinstance(slot["start_time"], str) else slot["start_time"]
            b = time.fromisoformat(slot["end_time"]) if isinstance(slot["end_time"], str) else slot["end_time"]
            finish = _local(day + timedelta(days=int(b <= a)), b, zone, end=True)
            # Ein Ausnahmetag ersetzt auch Ueberhaenge des Vortags.
            next_day = day + timedelta(days=1)
            if next_day in schedule.exceptions:
                finish = min(finish, day_end)
            result.append((begin, finish, (int(exception), slot.get("priority", 0), datetime.combine(day, a),
                                           slot.get("id", index)), slot.get("power", "on"),
                           slot.get("rotation_json", slot.get("rotation", []))))
        day += timedelta(days=1)
    return result


def timeline(schedule: Schedule | None, now_utc: datetime, hours: int = 72) -> list[dict]:
    start = utc(now_utc)
    end = start + timedelta(hours=hours)
    intervals = _intervals(schedule, start, end) if schedule else []
    boundaries = sorted({start, end} | {v for row in intervals for v in row[:2] if start < v < end})
    result: list[dict] = []
    for begin, finish in zip(boundaries, boundaries[1:], strict=False):
        candidates = [row for row in intervals if row[0] <= begin < row[1]]
        winner = max(candidates, key=lambda row: row[2]) if candidates else None
        power, rotation = (winner[3], winner[4]) if winner else ("on", None)
        if result and result[-1]["power"] == power and result[-1]["rotation_ref"] == rotation:
            result[-1]["until"] = iso(finish)
        else:
            result.append({"from": iso(begin), "until": iso(finish), "power": power,
                           "rotation_ref": rotation})
    return result


def current_slot(schedule: Schedule | None, now_utc: datetime) -> tuple[str, Any, datetime | None] | None:
    if schedule is None:
        return None
    now = utc(now_utc)
    rows = _intervals(schedule, now, now + timedelta(days=4))
    candidates = [row for row in rows if row[0] <= now < row[1]]
    if not candidates:
        return None
    winner = max(candidates, key=lambda row: row[2])
    # Naechste Grenze, an der sich der Gewinner aendern kann.
    future = [v for row in rows for v in row[:2] if v > now]
    return winner[3], winner[4], min(future) if future else None


def resolve(display: Display, now_utc: datetime, aktivitaet: Aktivitaet,
            overrides: list[dict], schedule: Schedule | None) -> dict:
    now = utc(now_utc)
    mode, power, url, until = "default", "on", display.url, None
    active = [o for o in overrides if o.get("org_id") == display.org_id
              and o.get("display_id") in (None, display.id) and not o.get("aufgehoben_am")
              and utc(o["von"]) <= now < utc(o["bis"])
              and not (o["kind"] == "gsl_page" and aktivitaet.gsl_ende
                       and utc(o["von"]) <= utc(aktivitaet.gsl_ende))]
    active.sort(key=lambda o: (utc(o["von"]), o.get("id", 0)), reverse=True)
    gsl_override = next((o for o in active if o["kind"] == "gsl_page"), None)
    manual = next((o for o in active if o["kind"] == "manual"), None)
    ende = utc(aktivitaet.letztes_ende) if aktivitaet.letztes_ende else None
    nachlauf = ende + timedelta(minutes=display.nachlauf_min) if ende else None
    slot = current_slot(schedule, now)
    override = None
    if display.locked:
        mode, url = "locked", "/static/infoscreen-agent/locked.html"
    elif aktivitaet.gsl and display.gsl_override_erlaubt and gsl_override:
        mode, override = "override", gsl_override
    elif aktivitaet.gsl:
        mode = "gsl"
    elif aktivitaet.incident:
        mode = "incident"
    elif ende is not None and ende <= now and nachlauf is not None and now < nachlauf:
        mode, until = "nachlauf", nachlauf
    elif manual:
        mode, override = "manual", manual
    elif slot:
        mode, power, _, until = "schedule", *slot
    if override:
        url, until = override["url"], utc(override["bis"])
    # Gueltigkeit konservativ an jedem Override-Beginn/-Ende begrenzen.
    starts = [utc(o[key]) for o in overrides if o.get("org_id") == display.org_id
              and o.get("display_id") in (None, display.id) and not o.get("aufgehoben_am")
              for key in ("von", "bis") if utc(o[key]) > now]
    if schedule and mode in ("schedule", "default"):
        starts.extend(v for row in _intervals(schedule, now, now + timedelta(days=8))
                      for v in row[:2] if v > now)
    if starts:
        until = min([until, *starts] if until else starts)
    return {"revision": display.revision, "rotation_ref": slot[1] if mode == "schedule" and slot else None,
            "mode": mode, "display_power": power, "url": url,
            "reason": mode, "valid_until": iso(until) if until else None,
            "config_version": display.config_version,
            "update_allowed": not (aktivitaet.gsl or aktivitaet.incident
                                   or mode in ("nachlauf", "locked")
                                   or (ende is not None and ende <= now and nachlauf is not None and now < nachlauf)),
            "server_time": iso(now)}
