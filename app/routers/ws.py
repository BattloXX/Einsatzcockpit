"""WebSocket endpoint – per-incident pub/sub channel.

Sicherheit:
- /ws/incident/{id}: Session-Cookie wird ausgewertet, User muss angemeldet sein
  UND can_access_incident() für den Einsatz erfüllen.
- /ws/global: nur eingeloggte Benutzer. Globale Einsatz-Benachrichtigungen sind
  org-spezifisch — der Broadcaster filtert nach org_id, hier reicht Auth.
- /ws/sms-gateway: Token-Auth (Bearer-Header oder ?token=). Für SMS-Gateway-Container.
"""
import asyncio
import json
import logging
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.permissions import can_access_incident
from app.core.security import hash_api_key, unsign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.major_incident import MajorIncident
from app.models.user import SmsGatewayToken, User
from app.services.broadcast import LAGE_WS_OFFSET, ORG_WS_OFFSET, manager

logger = logging.getLogger("einsatzleiter.ws")
router = APIRouter()

# ── SMS-Gateway Registry ───────────────────────────────────────────────────────
# org_id → Menge aktiver Gateway-WebSocket-Verbindungen
_sms_gateways: dict[int, set[WebSocket]] = defaultdict(set)
# job_id → asyncio.Future für sms.result-Rückmeldung
_sms_pending: dict[str, asyncio.Future] = {}


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
    set_tenant_context(db, None)
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
    set_tenant_context(db, None)
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
    set_tenant_context(db, None)
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
    """Org-spezifischer globaler Kanal – neue Einsätze, Einladungen etc.

    Auth erforderlich; Kanal = ORG_WS_OFFSET + user.org_id (org-isoliert).
    """
    user = _resolve_user(websocket)
    if user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    org_channel = ORG_WS_OFFSET + user.org_id if user.org_id else 0
    await manager.connect(org_channel, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await manager.disconnect(org_channel, websocket)


# ── SMS-Gateway WebSocket ──────────────────────────────────────────────────────

def _resolve_sms_gateway_token(websocket: WebSocket) -> SmsGatewayToken | None:
    """Liest Bearer-Token aus Authorization-Header oder ?token= Query-Param."""
    raw = (
        websocket.headers.get("authorization", "")
        or websocket.query_params.get("token", "")
    )
    if raw.lower().startswith("bearer "):
        raw = raw[7:]
    raw = raw.strip()
    if not raw:
        return None
    token_hash = hash_api_key(raw)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return (
            db.query(SmsGatewayToken)
            .filter(SmsGatewayToken.token_hash == token_hash, SmsGatewayToken.revoked_at.is_(None))
            .first()
        )
    finally:
        db.close()


def _touch_sms_gateway_token(token_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        tok = db.get(SmsGatewayToken, token_id)
        if tok:
            tok.last_used_at = datetime.now(UTC)
            db.commit()
    finally:
        db.close()


@router.websocket("/ws/sms-gateway")
async def sms_gateway_ws(websocket: WebSocket):
    """WebSocket-Kanal für den SMS-Gateway-Docker-Container.

    Auth per Bearer-Token (Authorization-Header oder ?token=).
    Der Container verbindet sich ausgehend und bleibt persistent verbunden.
    """
    token = _resolve_sms_gateway_token(websocket)
    if token is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    org_id = token.org_id
    token_id = token.id
    _touch_sms_gateway_token(token_id)

    await websocket.accept()
    _sms_gateways[org_id].add(websocket)
    logger.info("SMS-Gateway verbunden (org_id=%s, token_id=%s)", org_id, token_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")
            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif msg_type == "pong":
                pass
            elif msg_type == "sms.result":
                job_id = msg.get("id")
                fut = _sms_pending.pop(job_id, None)
                if fut and not fut.done():
                    fut.set_result(msg)
            else:
                logger.debug("SMS-Gateway unbekannter Typ: %s", msg_type)

    except WebSocketDisconnect:
        logger.info("SMS-Gateway getrennt (org_id=%s)", org_id)
    finally:
        _sms_gateways[org_id].discard(websocket)


def is_sms_gateway_connected(org_id: int) -> bool:
    """Gibt True zurück wenn mindestens ein SMS-Gateway für diese Org verbunden ist."""
    return bool(_sms_gateways.get(org_id))


async def dispatch_sms(org_id: int, job_id: str, to: str, text: str, timeout: float = 15.0) -> dict:
    """Sendet einen SMS-Job an einen verbundenen Gateway und wartet auf das Ergebnis.

    Rückgabe: sms.result-Dict (ok, error, provider_response).
    Wirft RuntimeError wenn kein Gateway verbunden oder Timeout überschritten.
    """
    gateways = list(_sms_gateways.get(org_id, []))
    if not gateways:
        raise RuntimeError(f"Kein SMS-Gateway für org_id={org_id} verbunden")

    ws = gateways[0]
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _sms_pending[job_id] = fut

    payload = json.dumps({"type": "sms.send", "id": job_id, "to": to, "text": text}, ensure_ascii=False)
    try:
        await ws.send_text(payload)
        return await asyncio.wait_for(fut, timeout=timeout)
    except TimeoutError:
        _sms_pending.pop(job_id, None)
        raise RuntimeError(f"SMS-Gateway Timeout für Job {job_id}")
    except Exception:
        _sms_pending.pop(job_id, None)
        raise
