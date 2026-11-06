"""Validierung, Deduplizierung und Aufbewahrung von Offline-Ereignissen."""
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.breathing import BreathingClientEvent


def valid_client_event_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9-]{8,40}", value):
        raise ValueError("Ungültige Ereignis-ID.")
    return value


def parse_erfasst_at(value: str | None, now: datetime) -> tuple[datetime, bool]:
    now = now.astimezone(UTC).replace(tzinfo=None) if now.tzinfo else now
    if not value:
        return now, False
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            raise ValueError
        ts = ts.astimezone(UTC).replace(tzinfo=None)
    except (ValueError, OverflowError):
        raise ValueError("Ungültiger Zeitstempel.") from None
    if ts - now > timedelta(seconds=120):
        ts = now
    if now - ts > timedelta(hours=12):
        raise ValueError("Der Zeitstempel ist zu alt.")
    return ts, now - ts > timedelta(seconds=30)


def event_audit(ts: datetime | None, nachgetragen: bool = False) -> dict:
    if ts is not None:
        return {"erfasst_at": ts.isoformat(), "nachgetragen": nachgetragen}
    return {"nachgetragen": True} if nachgetragen else {}


def claim_client_event(
    db: Session, incident_id: int, user_id: int | None, client_event_id: str,
    route: str, erfasst_at: datetime | None, nachgetragen: bool,
) -> bool:
    # SQLite's legacy transaction mode does not BEGIN for a preceding SELECT.
    # Ensure releasing the savepoint cannot commit the claim independently.
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        driver = connection.connection.driver_connection
        if driver is not None and not driver.in_transaction:
            connection.exec_driver_sql("BEGIN")
    try:
        with db.begin_nested():
            db.add(BreathingClientEvent(
                incident_id=incident_id, user_id=user_id, client_event_id=client_event_id,
                route=route, erfasst_at=erfasst_at, nachgetragen=nachgetragen,
                empfangen_at=datetime.now(UTC).replace(tzinfo=None),
            ))
            db.flush()
    except IntegrityError:
        return False
    return True


def cleanup_client_events(db: Session, now: datetime, batch: int = 200) -> bool:
    rows = db.query(BreathingClientEvent).filter(
        BreathingClientEvent.empfangen_at < now - timedelta(days=14),
    ).order_by(BreathingClientEvent.empfangen_at, BreathingClientEvent.id).limit(batch).all()
    for row in rows:
        db.delete(row)
    return bool(rows)
