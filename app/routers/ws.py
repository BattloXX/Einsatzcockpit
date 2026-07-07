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
from app.services.broadcast import LAGE_WS_OFFSET, ORG_WS_OFFSET, broadcast_org, manager
from app.services.sms_inbox_service import process_inbound_sms, record_inbound_sms

logger = logging.getLogger("einsatzleiter.ws")
router = APIRouter()

# ── SMS-Gateway Registry ───────────────────────────────────────────────────────
# org_id → aktive Gateway-WebSocket-Verbindungen, geordnet nach Verbindungszeit
# (ältester zuerst, neuester zuletzt). dispatch_sms bevorzugt die neueste und
# entfernt tote Sockets, damit doppelte/halboffene Verbindungen nicht den Versand
# blockieren (siehe Android-Reconnect-Verhalten).
_sms_gateways: dict[int, list[WebSocket]] = defaultdict(list)
# job_id → asyncio.Future für sms.result-Rückmeldung
_sms_pending: dict[str, asyncio.Future] = {}

# ── Print & Alarm Gateway (ECPG) Registry ──────────────────────────────────────
# org_id → aktive Gateway-WebSocket-Verbindungen (Muster _sms_gateways).
_print_gateways: dict[int, list[WebSocket]] = defaultdict(list)
# job_id(str) → Future für die erste job_status-Rückmeldung (dispatch_print_job)
_job_pending: dict[str, asyncio.Future] = {}


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


def _sms_receive_enabled(org_id: int) -> bool:
    """Liest OrgSettings.sms_receive_enabled – bestimmt ob die App RECEIVE_SMS anfordern soll."""
    from app.models.master import OrgSettings

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        return bool(settings and settings.sms_receive_enabled)
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
    _sms_gateways[org_id].append(websocket)
    logger.info(
        "SMS-Gateway verbunden (org_id=%s, token_id=%s, aktive=%d)",
        org_id, token_id, len(_sms_gateways[org_id]),
    )

    # Teilt der App mit, ob SMS-Empfang für diese Org aktiv ist – nur dann fordert
    # die App RECEIVE_SMS an und registriert ihren Empfangs-Receiver.
    await websocket.send_text(json.dumps({
        "type": "config", "receive_enabled": _sms_receive_enabled(org_id),
    }))

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
            elif msg_type == "sms.received":
                from_number = msg.get("from", "")
                text = msg.get("text", "")
                if from_number:
                    inbox_id = record_inbound_sms(org_id, token_id, from_number, text)
                    asyncio.create_task(process_inbound_sms(inbox_id))
                ack_id = msg.get("id")
                if ack_id:
                    await websocket.send_text(json.dumps({"type": "sms.received.ack", "id": ack_id}))
            else:
                logger.debug("SMS-Gateway unbekannter Typ: %s", msg_type)

    except WebSocketDisconnect:
        logger.info("SMS-Gateway getrennt (org_id=%s)", org_id)
    finally:
        _discard_gateway(org_id, websocket)


def is_sms_gateway_connected(org_id: int) -> bool:
    """Gibt True zurück wenn mindestens ein SMS-Gateway für diese Org verbunden ist."""
    return bool(_sms_gateways.get(org_id))


def _discard_gateway(org_id: int, websocket: WebSocket) -> None:
    """Entfernt eine Gateway-Verbindung aus der Registry (idempotent)."""
    try:
        _sms_gateways.get(org_id, []).remove(websocket)
    except ValueError:
        pass


async def dispatch_sms(org_id: int, job_id: str, to: str, text: str, timeout: float = 15.0) -> dict:
    """Sendet einen SMS-Job an einen verbundenen Gateway und wartet auf das Ergebnis.

    Bei mehreren registrierten Verbindungen (z. B. nach einem Client-Reconnect, der
    eine halboffene Verbindung hinterlassen hat) wird die **neueste** zuerst versucht.
    Schlägt das reine Senden fehl, gilt die Verbindung als tot: sie wird aus der
    Registry entfernt und die nächste Verbindung versucht. Ein *Timeout* (Senden
    erfolgreich, aber keine Antwort) wird hingegen NICHT erneut versucht, um doppelte
    SMS zu vermeiden.

    Rückgabe: sms.result-Dict (ok, error, provider_response).
    Wirft RuntimeError wenn kein Gateway verbunden oder Timeout überschritten.
    """
    gateways = list(_sms_gateways.get(org_id, []))
    if not gateways:
        raise RuntimeError(f"Kein SMS-Gateway für org_id={org_id} verbunden")

    payload = json.dumps({"type": "sms.send", "id": job_id, "to": to, "text": text}, ensure_ascii=False)
    loop = asyncio.get_event_loop()
    last_error: Exception | None = None

    # Neueste Verbindung zuerst
    for ws in reversed(gateways):
        fut: asyncio.Future = loop.create_future()
        _sms_pending[job_id] = fut
        try:
            await ws.send_text(payload)
        except Exception as exc:
            # Senden fehlgeschlagen → Verbindung tot: entfernen und nächste versuchen
            _sms_pending.pop(job_id, None)
            _discard_gateway(org_id, ws)
            last_error = exc
            logger.warning("SMS-Gateway-Verbindung tot, entferne und versuche nächste: %s", exc)
            continue

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            # Senden gelang, aber keine Antwort – nicht erneut versuchen (Doppelversand)
            _sms_pending.pop(job_id, None)
            raise RuntimeError(f"SMS-Gateway Timeout für Job {job_id}")
        except Exception:
            _sms_pending.pop(job_id, None)
            raise

    raise RuntimeError(
        f"Kein erreichbares SMS-Gateway für org_id={org_id} (letzter Fehler: {last_error})"
    )


# ── Print & Alarm Gateway (ECPG) WebSocket ─────────────────────────────────────

def _resolve_gateway(websocket: WebSocket):
    """Löst das Bearer-Device-Token zu einem Gateway auf (Muster SMS-Gateway)."""
    from app.models.gateway import Gateway

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
        gw = (
            db.query(Gateway)
            .filter(Gateway.device_token_hash == token_hash)
            .first()
        )
        if gw is not None:
            _ = gw.org_id, gw.id  # eager vor Session-Close
        return gw
    finally:
        db.close()


def _touch_gateway(gateway_id: int, *, version: str | None = None) -> None:
    from app.models.gateway import Gateway
    from app.services.gateway_service import mark_seen

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = db.get(Gateway, gateway_id)
        if gw:
            mark_seen(db, gw, version=version)
            db.commit()
    finally:
        db.close()


def _gateway_config_sync(gateway_id: int) -> dict:
    from app.models.gateway import Gateway
    from app.services.gateway_service import build_config_sync

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = db.get(Gateway, gateway_id)
        if not gw:
            return {}
        return build_config_sync(db, gw)
    finally:
        db.close()


def _apply_job_status(job_id: int, status: str, error: str | None) -> int | None:
    """Schreibt job_status vom Gateway in die DB. Gibt org_id zurück (für Broadcast)."""
    from app.models.gateway import JOB_TERMINAL, PrintJob

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        job = db.get(PrintJob, job_id)
        if not job:
            return None
        job.status = status
        if error:
            job.error = error[:500]
        db.commit()
        return job.org_id
    finally:
        db.close()


def _set_serial_status(gateway_id: int, connected: bool) -> int | None:
    from app.models.gateway import Gateway

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = db.get(Gateway, gateway_id)
        if not gw:
            return None
        gw.serial_connected = connected
        db.commit()
        return gw.org_id
    finally:
        db.close()


@router.websocket("/ws/gateway")
async def print_gateway_ws(websocket: WebSocket):
    """WebSocket-Kanal für den Print & Alarm Gateway-Container (ausgehend, persistent).

    Auth per Bearer-Device-Token (Authorization-Header oder ?token=). Bei Connect
    wird sofort config_sync gepusht. Nachrichten-Typen siehe Konzept Abschnitt 6.
    """
    gateway = _resolve_gateway(websocket)
    if gateway is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    org_id = gateway.org_id
    gateway_id = gateway.id

    await websocket.accept()
    _print_gateways[org_id].append(websocket)
    _touch_gateway(gateway_id)
    await broadcast_org(org_id, {"type": "gateway_status", "gateway_id": gateway_id, "online": True})
    logger.info("ECPG-Gateway verbunden (org_id=%s, gateway_id=%s)", org_id, gateway_id)

    # config_sync sofort pushen
    await websocket.send_text(json.dumps({
        "type": "config_sync", "payload": _gateway_config_sync(gateway_id),
    }, ensure_ascii=False))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            if mtype == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif mtype in ("pong", "log_event", "ack"):
                pass
            elif mtype == "hello":
                _touch_gateway(gateway_id, version=(msg.get("payload") or {}).get("version"))
                await websocket.send_text(json.dumps({
                    "type": "config_sync", "payload": _gateway_config_sync(gateway_id),
                }, ensure_ascii=False))
            elif mtype == "heartbeat":
                _touch_gateway(gateway_id)
            elif mtype == "job_status":
                p = msg.get("payload") or {}
                jid = p.get("job_id")
                # Future auflösen (falls dispatch_print_job auf Zustellung wartet)
                fut = _job_pending.pop(str(jid), None)
                if fut and not fut.done():
                    fut.set_result(p)
                if jid is not None:
                    o = _apply_job_status(int(jid), p.get("status", ""), p.get("error"))
                    if o:
                        await broadcast_org(o, {"type": "print_job_status", "job_id": int(jid),
                                                "status": p.get("status")})
            elif mtype == "serial_status":
                p = msg.get("payload") or {}
                o = _set_serial_status(gateway_id, bool(p.get("connected")))
                if o:
                    await broadcast_org(o, {"type": "gateway_serial", "gateway_id": gateway_id,
                                            "connected": bool(p.get("connected"))})
            elif mtype == "printer_report":
                from app.services.printer_report_service import apply_printer_report
                apply_printer_report(gateway_id, org_id, msg.get("payload") or {})
                await broadcast_org(org_id, {"type": "printer_report", "gateway_id": gateway_id})
            elif mtype == "alarm_notice":
                # Signal – der verbindliche Ingest läuft über REST POST /alarms.
                logger.info("ECPG alarm_notice (org_id=%s): %s", org_id,
                            (msg.get("payload") or {}).get("raw_hash"))
            else:
                logger.debug("ECPG-Gateway unbekannter Typ: %s", mtype)

    except WebSocketDisconnect:
        logger.info("ECPG-Gateway getrennt (org_id=%s, gateway_id=%s)", org_id, gateway_id)
    finally:
        _discard_print_gateway(org_id, websocket)
        _mark_gateway_offline(gateway_id)
        await broadcast_org(org_id, {"type": "gateway_status", "gateway_id": gateway_id, "online": False})


def _discard_print_gateway(org_id: int, websocket: WebSocket) -> None:
    try:
        _print_gateways.get(org_id, []).remove(websocket)
    except ValueError:
        pass


def _mark_gateway_offline(gateway_id: int) -> None:
    from app.models.gateway import GATEWAY_STATUS_OFFLINE, Gateway

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = db.get(Gateway, gateway_id)
        # Nur offline setzen wenn keine weitere Verbindung dieser Org mehr offen ist.
        if gw and not _print_gateways.get(gw.org_id):
            gw.status = GATEWAY_STATUS_OFFLINE
            gw.serial_connected = False
            db.commit()
    finally:
        db.close()


def is_gateway_connected(org_id: int) -> bool:
    """True, wenn mindestens ein ECPG-Gateway dieser Org verbunden ist."""
    return bool(_print_gateways.get(org_id))


async def push_config_sync(org_id: int, gateway_id: int) -> None:
    """Sendet aktualisierte config_sync an alle verbundenen Gateways der Org."""
    payload = json.dumps({"type": "config_sync", "payload": _gateway_config_sync(gateway_id)},
                         ensure_ascii=False)
    for ws in list(_print_gateways.get(org_id, [])):
        try:
            await ws.send_text(payload)
        except Exception:
            _discard_print_gateway(org_id, ws)


async def push_gateway_command(org_id: int, message: dict) -> bool:
    """Sendet ein Kommando (discover_printers, probe_printer, test_page, cancel_job,
    update_available) an die Gateways der Org. Gibt True bei mind. einem Empfänger."""
    payload = json.dumps(message, ensure_ascii=False)
    sent = False
    for ws in list(_print_gateways.get(org_id, [])):
        try:
            await ws.send_text(payload)
            sent = True
        except Exception:
            _discard_print_gateway(org_id, ws)
    return sent


async def dispatch_print_job(org_id: int, job_id: int, payload: dict, timeout: float = 20.0) -> dict:
    """Sendet einen print_job an ein verbundenes Gateway und wartet auf die erste
    job_status-Rückmeldung (Muster dispatch_sms, ohne Doppel-Retry bei Timeout)."""
    gateways = list(_print_gateways.get(org_id, []))
    if not gateways:
        raise RuntimeError(f"Kein Gateway für org_id={org_id} verbunden")

    key = str(job_id)
    msg = json.dumps({"type": "print_job", "id": key, "payload": payload}, ensure_ascii=False)
    loop = asyncio.get_event_loop()
    last_error: Exception | None = None

    for ws in reversed(gateways):
        fut: asyncio.Future = loop.create_future()
        _job_pending[key] = fut
        try:
            await ws.send_text(msg)
        except Exception as exc:
            _job_pending.pop(key, None)
            _discard_print_gateway(org_id, ws)
            last_error = exc
            continue
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            # Senden gelang → nicht erneut versuchen (kein Doppeldruck). Job bleibt
            # 'sent'; das Gateway spoolt und meldet den Endstatus asynchron nach.
            _job_pending.pop(key, None)
            return {"job_id": job_id, "status": "sent", "note": "timeout_waiting_status"}
        except Exception:
            _job_pending.pop(key, None)
            raise

    raise RuntimeError(f"Kein erreichbares Gateway für org_id={org_id} (letzter Fehler: {last_error})")
