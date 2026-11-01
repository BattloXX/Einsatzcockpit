"""Registrierung und Rotation der ActivityKit-Tokens."""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.live_activity import LiveActivityToken
from app.models.user import User


def upsert_token(
    db: Session, user: User, kind: str, token: str, incident_id: int | None, device_token_id: int | None,
) -> LiveActivityToken:
    if kind == "start":
        # Auch Sitzungen ohne Pairing-Bezug haben nur einen Start-Token je Benutzer.
        old = db.query(LiveActivityToken).filter(
            LiveActivityToken.user_id == user.id, LiveActivityToken.device_token_id == device_token_id,
            LiveActivityToken.kind == "start", LiveActivityToken.token != token,
        ).all()
        for previous in old:
            db.delete(previous)
    row = db.query(LiveActivityToken).filter_by(token=token).first()
    if row is None:
        row = LiveActivityToken(token=token)
        db.add(row)
    row.user_id = user.id
    row.device_token_id = device_token_id
    row.kind = kind
    row.incident_id = incident_id if kind == "activity" else None
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    row.last_error = None
    db.flush()
    return row
