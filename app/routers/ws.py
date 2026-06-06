"""WebSocket endpoint – per-incident pub/sub channel.

Sicherheit:
- /ws/incident/{id}: Session-Cookie wird ausgewertet, User muss angemeldet sein
  UND can_access_incident() für den Einsatz erfüllen.
- /ws/global: nur eingeloggte Benutzer. Globale Einsatz-Benachrichtigungen sind
  org-spezifisch — der Broadcaster filtert nach org_id, hier reicht Auth.
"""
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.permissions import can_access_incident
from app.core.security import unsign_session
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.major_incident import MajorIncident
from app.models.user import User
from app.services.broadcast import LAGE_WS_OFFSET, manager

logger = logging.getLogger("einsatzleiter.ws")
router = APIRouter()


# Close-Codes per RFC6455 (4000-4999 ist Application-Range)
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403


def _resolve_user(websocket: WebSocket) -> User | None:
    """Liest das Session-Cookie aus der Handshake-Anfrage und lädt den User."""
    token = websocket.cookies.get("session")
    if not token:
        return None
    session_data = unsign_session(token)
    if not session_data:
        return None
    user_id, *_ = session_data
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id, User.active == True).first()  # noqa: E712
        if user is not None:
            # Lazy-Loaded Beziehungen sicherstellen, bevor die Session zugeht
            _ = [r.code for r in user.roles]
            _ = user.org_id
        return user
    finally:
        db.close()


@router.websocket("/ws/incident/{incident_id}")
async def incident_ws(websocket: WebSocket, incident_id: int):
    user = _resolve_user(websocket)
    if user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    # Org-/Einsatz-Zugriff prüfen
    db = SessionLocal()
    try:
        incident = db.get(Incident, incident_id)
        if incident is None:
            await websocket.close(code=WS_CLOSE_FORBIDDEN)
            return
        # collaborating_orgs eager laden für can_access_incident
        _ = list(incident.collaborating_orgs or [])
        allowed = can_access_incident(user, incident)
    finally:
        db.close()

    if not allowed:
        await websocket.close(code=WS_CLOSE_FORBIDDEN)
        return

    await manager.connect(incident_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            # andere Nachrichten ignorieren wir bewusst (Server-Push only)
    except WebSocketDisconnect:
        await manager.disconnect(incident_id, websocket)


@router.websocket("/ws/lage/{lage_id}")
async def lage_ws(websocket: WebSocket, lage_id: int):
    """WebSocket-Kanal für eine Großschadenslage – org-gebunden."""
    user = _resolve_user(websocket)
    if user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    db = SessionLocal()
    try:
        lage = db.get(MajorIncident, lage_id)
        if lage is None or lage.org_id != user.org_id:
            from app.core.permissions import has_role
            if not (lage and has_role(user, "system_admin")):
                await websocket.close(code=WS_CLOSE_FORBIDDEN)
                return
    finally:
        db.close()

    channel = LAGE_WS_OFFSET + lage_id
    await manager.connect(channel, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await manager.disconnect(channel, websocket)


@router.websocket("/ws/global")
async def global_ws(websocket: WebSocket):
    """Global channel – empfängt new-incident-Benachrichtigungen.

    Auth erforderlich; org-Filter passiert serverseitig im Broadcaster.
    """
    user = _resolve_user(websocket)
    if user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    await manager.connect(0, websocket)  # 0 = global channel
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await manager.disconnect(0, websocket)
