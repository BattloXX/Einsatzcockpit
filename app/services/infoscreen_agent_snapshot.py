"""Org-gefilterte Eingaben und transaktionale Revisionen fuer den reinen Resolver."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from cryptography.fernet import InvalidToken
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.models.infoscreen_agent import (
    InfoscreenDevice,
    InfoscreenDeviceEvent,
    InfoscreenDisplay,
    InfoscreenOverride,
    InfoscreenSchedule,
    InfoscreenScheduleException,
    InfoscreenScheduleSlot,
)
from app.models.objekt import AlarmInfoscreenToken
from app.services.infoscreen_agent_resolver import Display, Schedule, resolve
from app.services.infoscreen_aktivitaet import lade_aktivitaet


def lade_schedule(db: Session, display: InfoscreenDisplay) -> Schedule | None:
    row = db.query(InfoscreenSchedule).filter(
        InfoscreenSchedule.id == display.schedule_id, InfoscreenSchedule.org_id == display.org_id).first()
    if row is None:
        return None
    slots = db.query(InfoscreenScheduleSlot).filter(
        InfoscreenScheduleSlot.schedule_id == row.id, InfoscreenScheduleSlot.org_id == display.org_id).all()
    exceptions = db.query(InfoscreenScheduleException).filter(
        InfoscreenScheduleException.schedule_id == row.id,
        InfoscreenScheduleException.org_id == display.org_id).all()
    return Schedule(row.timezone, [
        {key: getattr(slot, key) for key in ("id", "weekdays", "start_time", "end_time", "valid_from",
                                            "valid_to", "power", "priority", "rotation_json")}
        for slot in slots], {e.datum: e.slots_ersatz or [] for e in exceptions})


def alarm_url(db: Session, display: InfoscreenDisplay) -> str | None:
    token = db.query(AlarmInfoscreenToken).filter(
        AlarmInfoscreenToken.id == display.alarm_token_id, AlarmInfoscreenToken.org_id == display.org_id,
        AlarmInfoscreenToken.aktiv.is_(True)).first()
    if token is None or not token.token_enc:
        return None
    try:
        return "/infoscreen/alarm/" + decrypt_secret(token.token_enc)
    except (InvalidToken, ValueError):
        return None


def lade_overrides(db: Session, display: InfoscreenDisplay) -> list[dict]:
    from app.services.infoscreen_agent_overrides import page_url

    assert display.org_id is not None

    rows = db.query(InfoscreenOverride).filter(
        InfoscreenOverride.org_id == display.org_id,
        (InfoscreenOverride.display_id == display.id) | InfoscreenOverride.display_id.is_(None),
        InfoscreenOverride.aufgehoben_am.is_(None)).all()
    result = []
    for row in rows:
        try:
            url = page_url(db, display.org_id, row.url_kind, row.url_ref or "", stored=True)
        except ValueError:
            continue
        result.append({key: getattr(row, key) for key in
                       ("id", "org_id", "display_id", "kind", "url_kind", "von", "bis", "aufgehoben_am")}
                      | {"url": url})
    return result


def state(db: Session, device: InfoscreenDevice, display: InfoscreenDisplay, now: datetime) -> dict:
    """CAS mit dauerhaftem Hash: parallele Leser erhoehen nur einmal.

    Der Event enthaelt ausschliesslich technische IDs, niemals die Token-URL.
    """
    assert device.org_id is not None
    for _attempt in range(20):
        db.refresh(display)
        url = alarm_url(db, display)
        data = resolve(Display(display.id, device.org_id, url or "about:blank",
                               device.status != "active" or not display.aktiv or url is None,
                               display.gsl_override_erlaubt, display.nachlauf_min,
                               device.config_version, display.revision), now,
                       lade_aktivitaet(db, device.org_id), lade_overrides(db, display),
                       lade_schedule(db, display))
        digest = hashlib.sha256(json.dumps(
            {k: v for k, v in data.items() if k not in ("server_time", "revision")},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if display.resolution_hash == digest:
            return data
        previous = display.revision
        result = db.connection().execute(update(InfoscreenDisplay).where(
            InfoscreenDisplay.id == display.id, InfoscreenDisplay.org_id == device.org_id,
            InfoscreenDisplay.device_id == device.id, InfoscreenDisplay.revision == previous,
        ).values(revision=InfoscreenDisplay.revision + 1, resolution_hash=digest))
        if result.rowcount == 1:
            data["revision"] = previous + 1
            db.add(InfoscreenDeviceEvent(org_id=device.org_id, device_id=device.id, typ="resolution",
                                        revision=previous + 1,
                                        payload_json={"display_id": display.id}))
            db.commit()
            return data
        db.rollback()
    raise RuntimeError("Infoscreen-Revision konnte nicht atomar aktualisiert werden")
