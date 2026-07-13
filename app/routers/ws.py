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
import time
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
from app.services import ws_bus
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
# org_id → letzter Serial-Fan-Out-Status {enabled, listening, clients} (best effort, live).
_passthrough_status: dict[int, dict] = {}


# ── Lageführung: Presence + Soft-Locks (Phase 2) ────────────────────────────────
# Piggybackt auf dem bestehenden /ws/incident/{id}-Kanal statt einem eigenen Endpoint —
# nur Clients mit offener Lagekarte senden lagefuehrung.presence.*-Nachrichten, reine
# Board-Betrachter bleiben unsichtbar. Per-Worker In-Memory (Muster _sms_gateways/
# _passthrough_status oben); die Broadcasts selbst laufen über manager.broadcast() und
# damit bereits über ws_bus/Redis falls konfiguriert — nur die Locks sind pro Worker
# autoritativ (bewusst "weich": die eigentliche Konfliktsicherung läuft über
# version/409 in ui_lagefuehrung.py, ein Soft-Lock ist nur eine UI-Hilfe).
_LFT_LOCK_TTL_SECONDS = 15

# incident_id -> {websocket: {"user_id": int, "name": str}}
_lft_presence: dict[int, dict[WebSocket, dict]] = defaultdict(dict)
# incident_id -> {feature_id: {"user_id": int, "name": str, "expires_at": float}}
_lft_locks: dict[int, dict[int, dict]] = defaultdict(dict)


def _lft_presence_list(incident_id: int) -> list[dict]:
    return [
        {"user_id": info["user_id"], "name": info["name"]}
        for info in _lft_presence.get(incident_id, {}).values()
    ]


def _lft_purge_expired_locks(incident_id: int) -> None:
    now = time.monotonic()
    locks = _lft_locks.get(incident_id)
    if not locks:
        return
    for fid in [fid for fid, lock in locks.items() if lock["expires_at"] <= now]:
        del locks[fid]


def get_passthrough_status(org_id: int | None) -> dict | None:
    """Letzter gemeldeter Serial-Fan-Out-Status der Org (oder None)."""
    if org_id is None:
        return None
    return _passthrough_status.get(org_id)


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
    lft_joined = False
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
                continue
            try:
                msg = json.loads(data)
            except (ValueError, TypeError):
                continue
            msg_type = msg.get("type") if isinstance(msg, dict) else None

            if msg_type in ("lagefuehrung.presence.join", "lagefuehrung.presence.heartbeat"):
                _lft_presence[incident_id][websocket] = {"user_id": user.id, "name": user.display_name}
                lft_joined = True
                await manager.broadcast(incident_id, {
                    "type": "lagefuehrung.presence.changed",
                    "users": _lft_presence_list(incident_id),
                })
            elif msg_type == "lagefuehrung.feature.editing":
                feature_id = msg.get("feature_id")
                if not isinstance(feature_id, int):
                    continue
                _lft_purge_expired_locks(incident_id)
                existing = _lft_locks[incident_id].get(feature_id)
                if not existing or existing["user_id"] == user.id:
                    _lft_locks[incident_id][feature_id] = {
                        "user_id": user.id, "name": user.display_name,
                        "expires_at": time.monotonic() + _LFT_LOCK_TTL_SECONDS,
                    }
                    await manager.broadcast(incident_id, {
                        "type": "lagefuehrung.feature.locked",
                        "feature_id": feature_id, "user_id": user.id, "name": user.display_name,
                    })
            elif msg_type == "lagefuehrung.feature.released":
                feature_id = msg.get("feature_id")
                lock = _lft_locks.get(incident_id, {}).get(feature_id)
                if lock and lock["user_id"] == user.id:
                    del _lft_locks[incident_id][feature_id]
                    await manager.broadcast(incident_id, {
                        "type": "lagefuehrung.feature.unlocked", "feature_id": feature_id,
                    })
            # andere Nachrichten ignorieren wir bewusst (Server-Push only)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(incident_id, websocket)
        if lft_joined:
            _lft_presence[incident_id].pop(websocket, None)
            locks = _lft_locks.get(incident_id, {})
            released = [fid for fid, lock in locks.items() if lock["user_id"] == user.id]
            for fid in released:
                del locks[fid]
            try:
                await manager.broadcast(incident_id, {
                    "type": "lagefuehrung.presence.changed",
                    "users": _lft_presence_list(incident_id),
                })
                for fid in released:
                    await manager.broadcast(incident_id, {
                        "type": "lagefuehrung.feature.unlocked", "feature_id": fid,
                    })
            except Exception:
                logger.debug("Lageführung-Cleanup-Broadcast fehlgeschlagen (Verbindung bereits zu)", exc_info=True)


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
    from app.models.gateway import PrintJob

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
                # Future auflösen (falls dispatch_print_job auf Zustellung wartet).
                # Liegt es lokal → direkt; sonst (Bus aktiv) auf einem anderen
                # Worker → dorthin melden, damit dessen Future aufgelöst wird.
                fut = _job_pending.pop(str(jid), None)
                if fut and not fut.done():
                    fut.set_result(p)
                elif ws_bus.enabled() and jid is not None:
                    await ws_bus.publish(ws_bus.CH_GW, {
                        "kind": "job_status", "org_id": org_id, "job_id": jid, "payload": p,
                    })
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
            elif mtype == "printer_status":
                # Periodischer Erreichbarkeits-Check je Drucker (id-basiert).
                from app.services.printer_report_service import apply_printer_status
                apply_printer_status(gateway_id, org_id, msg.get("payload") or {})
                await broadcast_org(org_id, {"type": "printer_report", "gateway_id": gateway_id})
            elif mtype == "passthrough_status":
                p = msg.get("payload") or {}
                _passthrough_status[org_id] = {
                    "enabled": bool(p.get("enabled")),
                    "listening": bool(p.get("listening")),
                    "clients": int(p.get("clients") or 0),
                }
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
        if gw and gw.org_id is not None and not _print_gateways.get(gw.org_id):
            gw.status = GATEWAY_STATUS_OFFLINE
            gw.serial_connected = False
            db.commit()
    finally:
        db.close()


def is_gateway_connected(org_id: int) -> bool:
    """True, wenn ein ECPG-Gateway dieser Org an DIESEM Worker verbunden ist."""
    return bool(_print_gateways.get(org_id))


def gateway_online(org_id: int | None) -> bool:
    """True, wenn ein Gateway dieser Org verbunden ist – worker-übergreifend.

    Lokale Registry ODER DB-Heartbeat (status online + last_seen der letzten 2 Min):
    der Socket kann bei -w 2+ an einem ANDEREN Worker hängen. Grundlage für die
    Dispatch-Vorprüfung (schnelles Scheitern, statt 20 s auf ein Future zu warten,
    wenn gar kein Gateway verbunden ist)."""
    if org_id is None:
        return False
    if is_gateway_connected(org_id):
        return True
    from datetime import timedelta

    from app.models.gateway import GATEWAY_STATUS_ONLINE, Gateway
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=2)
        gw = (
            db.query(Gateway)
            .filter(
                Gateway.org_id == org_id,
                Gateway.device_token_hash.isnot(None),
                Gateway.status == GATEWAY_STATUS_ONLINE,
                Gateway.last_seen_at.isnot(None),
                Gateway.last_seen_at >= cutoff,
            )
            .first()
        )
        return gw is not None
    finally:
        db.close()


async def _send_to_local_gateways(org_id: int, message: dict) -> bool:
    """Sendet eine Nachricht an die an DIESEM Worker verbundenen Gateways der Org.

    Gemeinsamer Zusteller für den lokalen Pfad und den Bus-Handler."""
    payload = json.dumps(message, ensure_ascii=False)
    sent = False
    for ws in list(_print_gateways.get(org_id, [])):
        try:
            await ws.send_text(payload)
            sent = True
        except Exception:
            _discard_print_gateway(org_id, ws)
    return sent


async def _bus_gateway_deliver(payload: dict) -> None:
    """Bus-Handler (CH_GW): stellt Gateway-Nachrichten an lokale Sockets zu bzw.
    löst wartende Dispatch-Futures auf (worker-übergreifend)."""
    kind = payload.get("kind")
    # job_status meldet eine wartende Dispatch-Future (worker-lokal via job_id) auf -
    # kein org-gebundenes Zustellen an WebSockets, daher ohne org_id im Payload.
    if kind == "job_status":
        fut = _job_pending.pop(str(payload.get("job_id")), None)
        if fut and not fut.done():
            fut.set_result(payload.get("payload") or {})
        return
    org_id = payload.get("org_id")
    if not isinstance(org_id, int):
        return
    if kind == "print_job":
        await _send_to_local_gateways(org_id, {
            "type": "print_job", "id": str(payload.get("job_id")),
            "payload": payload.get("payload") or {},
        })
    elif kind == "command":
        await _send_to_local_gateways(org_id, payload.get("message") or {})
    elif kind == "config_sync":
        gateway_id = payload.get("gateway_id")
        if not isinstance(gateway_id, int):
            return
        await _send_to_local_gateways(org_id, {
            "type": "config_sync", "payload": _gateway_config_sync(gateway_id),
        })


ws_bus.register(ws_bus.CH_GW, _bus_gateway_deliver)


async def push_config_sync(org_id: int, gateway_id: int) -> None:
    """Sendet aktualisierte config_sync an alle verbundenen Gateways der Org."""
    if ws_bus.enabled():
        await ws_bus.publish(ws_bus.CH_GW, {
            "kind": "config_sync", "org_id": org_id, "gateway_id": gateway_id,
        })
        return
    await _send_to_local_gateways(org_id, {
        "type": "config_sync", "payload": _gateway_config_sync(gateway_id),
    })


async def push_gateway_command(org_id: int, message: dict) -> bool:
    """Sendet ein Kommando (discover_printers, probe_printer, test_page, cancel_job,
    update_available) an die Gateways der Org. Gibt True bei mind. einem Empfänger
    (im Bus-Modus: DB-Heartbeat, da der Socket an einem anderen Worker hängen kann)."""
    if ws_bus.enabled():
        await ws_bus.publish(ws_bus.CH_GW, {
            "kind": "command", "org_id": org_id, "message": message,
        })
        return gateway_online(org_id)
    return await _send_to_local_gateways(org_id, message)


async def dispatch_print_job(org_id: int, job_id: int, payload: dict, timeout: float = 20.0) -> dict:
    """Sendet einen print_job an ein verbundenes Gateway und wartet auf die erste
    job_status-Rückmeldung (Muster dispatch_sms, ohne Doppel-Retry bei Timeout).

    Bus-Modus (REDIS_URL gesetzt): Der Auftrag wird auf CH_GW publiziert – so
    erreicht er das Gateway auch, wenn dessen Socket an einem anderen Worker hängt.
    Die job_status-Rückmeldung löst das Future via Bus wieder auf. Ohne Redis läuft
    der bisherige In-Process-Pfad (_dispatch_print_job_local)."""
    if ws_bus.enabled():
        if not gateway_online(org_id):
            raise RuntimeError(f"Kein Gateway für org_id={org_id} verbunden")
        key = str(job_id)
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        _job_pending[key] = fut
        await ws_bus.publish(ws_bus.CH_GW, {
            "kind": "print_job", "org_id": org_id, "job_id": job_id, "payload": payload,
        })
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            _job_pending.pop(key, None)
            return {"job_id": job_id, "status": "sent", "note": "timeout_waiting_status"}
        except Exception:
            _job_pending.pop(key, None)
            raise
    return await _dispatch_print_job_local(org_id, job_id, payload, timeout)


async def _dispatch_print_job_local(org_id: int, job_id: int, payload: dict, timeout: float) -> dict:
    """In-Process-Zustellung (kein Redis): Socket muss an DIESEM Worker hängen."""
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
