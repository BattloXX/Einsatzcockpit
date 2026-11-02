"""UTC-Ausgabe fuer MCP; naive DB-Werte sind immer UTC."""
import re
from datetime import UTC, datetime


def iso_utc(value: datetime | str | None) -> str | None:
    if value is None or value == "":
        return None
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def timestamps(value):
    """Auch verschachtelte Service-/GitHub-Antworten zentral serialisieren."""
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:", value):
        return iso_utc(value) or value
    if isinstance(value, dict):
        return {key: timestamps(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [timestamps(child) for child in value]
    return value


def utc_response(fn):
    from functools import wraps

    @wraps(fn)
    def wrapped(*args, **kwargs):
        return timestamps(fn(*args, **kwargs))
    return wrapped


def log_timestamp(row: dict) -> str | None:
    """Neue UTC-Metadaten bevorzugen; alte UI-Zeit ist lokale Prozesszeit."""
    if row.get("ts_iso"):
        return iso_utc(row["ts_iso"])
    if not row.get("ts"):
        return None
    try:
        return iso_utc(datetime.fromisoformat(row["ts"]).astimezone(UTC))
    except (ValueError, OverflowError, OSError):
        return None
