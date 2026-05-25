"""Zeitzonen-Helper.

Konvertiert UTC-Datetimes (DB-Standard) in die Anzeige-Zeitzone der jeweiligen
Organisation. Faellt auf settings.DEFAULT_TIMEZONE zurueck, wenn die Org keine
eigene Zeitzone gesetzt hat.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from app.config import settings


def org_tz(org: Any | None) -> ZoneInfo:
    """Liefert die ZoneInfo-Instanz fuer eine Org (oder Default)."""
    name = getattr(org, "timezone", None) or settings.DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(settings.DEFAULT_TIMEZONE)


def to_org_tz(dt: datetime | None, org: Any | None = None) -> datetime | None:
    """Konvertiert ein UTC-Datetime in die Org-Zeitzone.

    Naive Datetimes werden als UTC angenommen.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(org_tz(org))


def format_local_time(dt: datetime | None, org: Any | None = None) -> str:
    """HH:MM in Org-Zeitzone."""
    local = to_org_tz(dt, org)
    return local.strftime("%H:%M") if local else ""


def format_local_datetime(dt: datetime | None, org: Any | None = None) -> str:
    """DD.MM.YYYY HH:MM in Org-Zeitzone."""
    local = to_org_tz(dt, org)
    return local.strftime("%d.%m.%Y %H:%M") if local else ""


def format_local_iso(dt: datetime | None, org: Any | None = None) -> str:
    """ISO-8601 mit Offset (fuer Frontend-Konsumenten wie Alpine-Timer)."""
    local = to_org_tz(dt, org)
    return local.isoformat() if local else ""


def common_timezones() -> list[str]:
    """Sortierte Liste haeufig benoetigter Zeitzonen + alle europaeischen."""
    preferred = [
        "Europe/Vienna",
        "Europe/Berlin",
        "Europe/Zurich",
        "Europe/London",
        "Europe/Paris",
        "Europe/Rome",
        "UTC",
    ]
    seen = set(preferred)
    rest = sorted(tz for tz in available_timezones() if tz not in seen)
    return preferred + rest
