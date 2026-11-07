"""Validierung der Verwaltungsformulare; Planzeiten bleiben lokale Wanduhrzeiten."""

from __future__ import annotations

import json
from datetime import date, time
from typing import Any

from sqlalchemy.orm import Session

from app.models.infoscreen_agent import InfoscreenScheduleSlot
from app.models.objekt import InfoscreenUrl

MAX_SLOTS = 200
MAX_EXCEPTIONS = 366
MAX_ROTATION = 30


def text(value: Any, maximum: int = 100, *, required: bool = True) -> str:
    result = str(value or "").strip()
    if any(ord(char) < 32 for char in result):
        raise ValueError("Text darf keine Steuerzeichen enthalten")
    if len(result) > maximum or (required and not result):
        raise ValueError(f"Text erforderlich, maximal {maximum} Zeichen")
    return result


def number(value: Any, minimum: int, maximum: int) -> int:
    result = int(str(value))
    if not minimum <= result <= maximum:
        raise ValueError(f"Zahl muss zwischen {minimum} und {maximum} liegen")
    return result


def choice(value: Any, allowed: tuple[str, ...]) -> str:
    result = str(value)
    if result not in allowed:
        raise ValueError("Ungueltige Auswahl")
    return result


def rotation(db: Session, org_id: int, value: str | list) -> list[dict]:
    if isinstance(value, str) and len(value) > 12000:
        raise ValueError("Rotation ist zu gross")
    rows = json.loads(value) if isinstance(value, str) else value
    if not isinstance(rows, list) or len(rows) > MAX_ROTATION:
        raise ValueError(f"Rotation: maximal {MAX_ROTATION} Eintraege")
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) not in ({"url_id", "dwell_sec"}, {"builtin", "dwell_sec"}):
            raise ValueError("Rotation braucht URL oder builtin weather und dwell_sec")
        dwell = number(row["dwell_sec"], 5, 3600)
        if "builtin" in row:
            result.append({"builtin": choice(row["builtin"], ("weather",)), "dwell_sec": dwell})
        else:
            url_id = number(row["url_id"], 1, 2**63 - 1)
            if (
                db.query(InfoscreenUrl.id)
                .filter(InfoscreenUrl.id == url_id, InfoscreenUrl.org_id == org_id, InfoscreenUrl.aktiv.is_(True))
                .first()
                is None
            ):
                raise ValueError("Rotation: URL ist nicht aktiv oder gehoert einer anderen Organisation")
            result.append({"url_id": url_id, "dwell_sec": dwell})
    return result


def slot_values(db: Session, org_id: int, data: dict) -> dict:
    start, end = time.fromisoformat(str(data["start_time"])), time.fromisoformat(str(data["end_time"]))
    if start.tzinfo or end.tzinfo or start == end:
        raise ValueError("Slotzeiten sind Wanduhrzeiten; Ende muss sich vom Start unterscheiden")
    valid_from = date.fromisoformat(data["valid_from"]) if data.get("valid_from") else None
    valid_to = date.fromisoformat(data["valid_to"]) if data.get("valid_to") else None
    if valid_from and valid_to and valid_to < valid_from:
        raise ValueError("Gueltig bis liegt vor Gueltig ab")
    return dict(
        weekdays=number(data.get("weekdays", 127), 1, 127),
        start_time=start,
        end_time=end,
        valid_from=valid_from,
        valid_to=valid_to,
        power=choice(data.get("power", "on"), ("on", "off")),
        priority=number(data.get("priority", 0), -1000, 1000),
        label=text(data.get("label"), 200, required=False),
        rotation_json=rotation(db, org_id, data.get("rotation_json", "[]")),
    )


def _minutes(clock: time) -> float:
    return clock.hour * 60 + clock.minute + clock.second / 60


def overlaps(a: dict, b: dict) -> bool:
    """Auch Uebernacht-Slots und Sonntag/Montag; Datumsgrenzen beruecksichtigen."""
    if a["priority"] != b["priority"]:
        return False
    for day_a in range(7):
        if not a["weekdays"] & (1 << day_a):
            continue
        for day_b in range(-1, 9):
            if not b["weekdays"] & (1 << (day_b % 7)):
                continue
            start_a, end_a = day_a * 1440 + _minutes(a["start_time"]), day_a * 1440 + _minutes(a["end_time"])
            start_b, end_b = day_b * 1440 + _minutes(b["start_time"]), day_b * 1440 + _minutes(b["end_time"])
            end_a += 1440 if end_a < start_a else 0
            end_b += 1440 if end_b < start_b else 0
            if max(start_a, start_b) >= min(end_a, end_b):
                continue
            # Startdaten der beiden Slots unterscheiden sich um day_b-day_a.
            delta = day_b - day_a
            lower = max((a["valid_from"] or date.min).toordinal(), (b["valid_from"] or date.min).toordinal() - delta, 1)
            upper = min(
                (a["valid_to"] or date.max).toordinal(),
                (b["valid_to"] or date.max).toordinal() - delta,
                date.max.toordinal(),
            )
            if lower > upper:
                continue
            candidate = lower + (day_a - date.fromordinal(lower).weekday()) % 7
            if candidate <= upper:
                return True
    return False


def check_overlap(db: Session, org_id: int, schedule_id: int, values: dict, slot_id: int = 0) -> bool:
    rows = (
        db.query(InfoscreenScheduleSlot)
        .filter(InfoscreenScheduleSlot.org_id == org_id, InfoscreenScheduleSlot.schedule_id == schedule_id)
        .all()
    )
    return any(row.id != slot_id and overlaps(values, {key: getattr(row, key) for key in values}) for row in rows)


def replacement_slots(db: Session, org_id: int, value: str, acknowledged: bool) -> list[dict]:
    if len(value) > 100000:
        raise ValueError("Ersatz-Slots sind zu gross")
    rows = json.loads(value)
    if not isinstance(rows, list) or len(rows) > MAX_SLOTS:
        raise ValueError(f"Maximal {MAX_SLOTS} Ersatz-Slots")
    result: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Ungueltiger Ersatz-Slot")
        values = slot_values(db, org_id, row | {"weekdays": 127, "valid_from": "", "valid_to": ""})
        if not acknowledged and any(overlaps(values, other) for other in result):
            raise ValueError("Warnung: Ersatz-Slots gleicher Prioritaet ueberlappen. Bitte bestaetigen.")
        result.append(values)
    return [{**r, "start_time": r["start_time"].isoformat(), "end_time": r["end_time"].isoformat()} for r in result]


def week_grid(slots: list[InfoscreenScheduleSlot]) -> list[list[dict]]:
    days: list[list[dict]] = [[] for _ in range(7)]
    for slot in slots:
        start, end = _minutes(slot.start_time), _minutes(slot.end_time)
        for day in range(7):
            if not slot.weekdays & (1 << day):
                continue
            segments = [(day, start, end)] if end > start else [(day, start, 1440), ((day + 1) % 7, 0, end)]
            for target, first, last in segments:
                days[target].append({"top": first / 1440 * 100, "height": (last - first) / 1440 * 100, "slot": slot})
    return days
