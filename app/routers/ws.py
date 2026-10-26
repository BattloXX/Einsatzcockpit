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
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.permissions import can_access_incident, has_role
from app.core.security import hash_api_key, pruefe_geraete_session, unsign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.major_incident import MajorIncident
from app.models.user import SmsGatewayToken, User
from app.services import gateway_presence, gateway_routing, ws_bus
from app.services.broadcast import EINHEIT_WS_OFFSET, LAGE_WS_OFFSET, ORG_WS_OFFSET, broadcast_org, manager
from app.services.gk_zugang_service import COOKIE as GK_COOKIE
from app.services.gk_zugang_service import QR_COOKIE, sitzung_pruefen_mit_grund
from app.services.sms_inbox_service import process_inbound_sms, record_inbound_sms

logger = logging.getLogger("einsatzleiter.ws")
router = APIRouter()

# ── SMS-Gateway Registry ───────────────────────────────────────────────────────
# org_id → (token_id, WebSocket)-Paare aktiver Gateway-Verbindungen. token_id wird
# mitgefuehrt, damit dispatch_sms() nach SmsGatewayToken.priority sortieren kann statt
# rein nach Verbindungszeit.
_sms_gateways: dict[int, list[tuple[int, WebSocket]]] = defaultdict(list)
SMS_GATEWAY_IDLE_PING_S = 30.0
SMS_GATEWAY_DEAD_AFTER_S = 120.0
# job_id → asyncio.Future für sms.result-Rückmeldung
_sms_pending: dict[str, asyncio.Future] = {}
# org_id -> zuletzt fuer eine Einzel-SMS ausgewaehltes Gateway.
_sms_last_gateway: dict[int, int] = {}
# asyncio behaelt Tasks nur schwach; diese Referenzen verhindern, dass ein
# Nachversand zwischen Gateway-Connect und Abschluss eingesammelt wird.
_sms_nachversand_tasks: set[asyncio.Task] = set()

# ── Print & Alarm Gateway (ECPG) Registry ──────────────────────────────────────
# org_id → (gateway_id, WebSocket)-Paare aktiver Gateway-Verbindungen.
_print_gateways: dict[int, list[tuple[int, WebSocket]]] = defaultdict(list)
# org_id → (gateway_id, WebSocket)-Paare verbundener Windows-Druckagents (/ws/print-agent).
_print_agents: dict[int, list[tuple[int, WebSocket]]] = defaultdict(list)
_print_agent_sessions: dict[WebSocket, tuple[int, int, str, str]] = {}
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
_LAGE_AUTH_CHECK_SECONDS = 25.0


def _resolve_user(websocket: WebSocket) -> User | None:
    """Liest das Session-Cookie aus der Handshake-Anfrage und lädt den User."""
    token = websocket.cookies.get("session")
    if not token:
        return None
    session_data = unsign_session(token)
    if not session_data:
        return None
    (
        user_id, _is_qr, _qr_incident_id, is_device, _display_name,
        _qr_lage_id, _is_remember, device_token_id,
    ) = session_data
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = db.query(User).filter(User.id == user_id, User.active == True).first()  # noqa: E712
        if user is not None and is_device and not pruefe_geraete_session(
            db, user_id, is_device, device_token_id,
        ):
            return None
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
    """Org-gebundener Lagekanal mit erneuter Zugangsprüfung, auch bei stillen Clients."""
    user = _resolve_user(websocket)
    if user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, lage_id)
        if lage is None or (lage.org_id != user.org_id and not has_role(user, "system_admin")):
            await websocket.close(code=WS_CLOSE_FORBIDDEN)
            return
    finally:
        db.close()

    channel = LAGE_WS_OFFSET + lage_id
    await manager.connect(channel, websocket)
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=_LAGE_AUTH_CHECK_SECONDS)
            except TimeoutError:
                data = None
            # Frische Session: Gerätewiderruf, deaktivierter Benutzer und Org-/Rollenwechsel
            # müssen auch einen bereits im Lagekanal registrierten Socket beenden.
            aktuell = _resolve_user(websocket)
            code = None
            if aktuell is None:
                code = WS_CLOSE_UNAUTHORIZED
            elif aktuell.org_id != lage.org_id and not has_role(aktuell, "system_admin"):
                code = WS_CLOSE_FORBIDDEN
            if code is not None:
                await websocket.send_json({"type": "zugang:widerrufen"})
                await websocket.close(code=code)
                break
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(channel, websocket)


def _resolve_gk_principal(websocket: WebSocket):
    """Prüft das GK-Cookie mit einer eigenen, tenantfreien DB-Session."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        for typ, cookie in (("personal", websocket.cookies.get(GK_COOKIE)), ("qr", websocket.cookies.get(QR_COOKIE))):
            if not cookie:
                continue
            principal, _grund = sitzung_pruefen_mit_grund(db, cookie, typ)
            if principal:
                return principal
            db.rollback()
        return None
    finally:
        db.close()


@router.websocket("/ws/einheit-zugang")
async def einheit_zugang_ws(websocket: WebSocket):
    """Minimaler, einheitengefilterter Echtzeitkanal für GK-Sitzungen.

    Widerruf wirkt spätestens beim nächsten Ping (der Client sendet ihn alle
    25 s); einen Post-Commit-Broadcast gibt es dafür nicht.
    """
    principal = _resolve_gk_principal(websocket)
    if principal is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    channel = EINHEIT_WS_OFFSET + principal.einheit.id
    await manager.connect(channel, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data != "ping":
                continue
            # Kein Berechtigungs-Cache: jede 25-s-Prüfung öffnet eine frische DB-Session.
            if _resolve_gk_principal(websocket) is None:
                await websocket.send_json({"type": "zugang:widerrufen"})
                await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
                return
            await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
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


def _heartbeat_sms_gateway_token(token_id: int) -> None:
    """Persistiert hoechstens einmal je Minute einen echten WS-Heartbeat."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        tok = db.get(SmsGatewayToken, token_id)
        now = datetime.now(UTC).replace(tzinfo=None)
        if tok and (tok.last_heartbeat_at is None or now - tok.last_heartbeat_at >= timedelta(seconds=60)):
            tok.last_heartbeat_at = now
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


def _start_sms_nachversand(org_id: int) -> None:
    """Startet den Nachversand nach einer neuen Gateway-Verbindung."""
    from app.services.sms_dispatch_service import retry_pending_einsatzinfo

    task = asyncio.create_task(retry_pending_einsatzinfo(org_id))
    _sms_nachversand_tasks.add(task)

    def _done(finished: asyncio.Task) -> None:
        _sms_nachversand_tasks.discard(finished)
        if not finished.cancelled() and finished.exception() is not None:
            logger.error("SMS-Nachversand fehlgeschlagen (org_id=%s)", org_id, exc_info=finished.exception())

    task.add_done_callback(_done)


@router.websocket("/ws/sms-gateway")
async def sms_gateway_ws(websocket: WebSocket):
    """WebSocket-Kanal für den SMS-Gateway-Docker-Container.

    Auth per Bearer-Token (Authorization-Header oder ?token=).
    Der Container verbindet sich ausgehend und bleibt persistent verbunden.
    """
    token = await asyncio.to_thread(_resolve_sms_gateway_token, websocket)
    if token is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    org_id = token.org_id
    token_id = token.id
    await asyncio.to_thread(_touch_sms_gateway_token, token_id)

    connection = await asyncio.to_thread(gateway_routing.begin, "sms", org_id, token_id, token.token_hash)
    if connection is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    try:
        await websocket.accept()
    except Exception:
        await asyncio.to_thread(gateway_routing.end, connection)
        raise
    gateway_routing.attach(connection, token.token_hash, websocket)
    connected_at = last_received = time.monotonic()
    # Registry vor dem ersten await aktualisieren: auch parallele Reconnects
    # koennen damit nur einen Slot je Token belegen.
    replaced = [ws for tid, ws in _sms_gateways[org_id] if tid == token_id]
    for old in replaced:
        _discard_gateway(org_id, old)
    _sms_gateways[org_id].append((token_id, websocket))
    logger.info(
        "SMS-Gateway verbunden (org_id=%s, token_id=%s, aktive=%d, connection_id=%s, worker_id=%s)",
        org_id, token_id, len(_sms_gateways[org_id]), connection.connection_id, connection.worker_id,
    )
    try:
        for old in replaced:
            try:
                await old.close(code=1012, reason="replaced")
            except Exception as exc:
                logger.warning(
                    "SMS-Gateway ersetzt, Schliessen fehlgeschlagen (org_id=%s, token_id=%s, %s): %s",
                    org_id, token_id, type(exc).__name__, exc,
                )
        _start_sms_nachversand(org_id)

        # RECEIVE_SMS nur anfordern, wenn der Empfang fuer diese Org aktiv ist.
        await websocket.send_text(json.dumps({
            "type": "config", "receive_enabled": await asyncio.to_thread(_sms_receive_enabled, org_id),
        }))
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=SMS_GATEWAY_IDLE_PING_S)
            except TimeoutError:
                if await asyncio.to_thread(gateway_routing.state, connection, token.token_hash) != "connected":
                    await websocket.close(code=4401)
                    break
                if time.monotonic() - last_received > SMS_GATEWAY_DEAD_AFTER_S:
                    await websocket.close(code=4408, reason="idle timeout")
                    logger.info(
                        "SMS-Gateway getrennt (org_id=%s, token_id=%s, code=4408, "
                        "reason=idle timeout, duration_s=%.1f)",
                        org_id, token_id, time.monotonic() - connected_at,
                    )
                    break
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue
            last_received = time.monotonic()
            if not await asyncio.to_thread(gateway_routing.touch, connection, token.token_hash):
                await websocket.close(code=4401)
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(msg, dict):
                continue
            msg_type = msg.get("type")
            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif msg_type == "pong":
                pass
            elif msg_type == "hello":
                from app.services import sms_outbox
                controls, abandoned = await asyncio.to_thread(sms_outbox.hello, connection, token.token_hash, msg)
                await sms_outbox.notify(controls)
                for notice in abandoned:
                    await websocket.send_json(notice)
            elif msg_type in ("sms.ack", "sms.attempt", "sms.result"):
                from app.services import sms_outbox
                applied, controls, status = await asyncio.to_thread(sms_outbox.event, connection, token.token_hash, msg)
                await sms_outbox.notify(controls)
                identity = sms_outbox.parse_wire(msg)
                if applied and identity:
                    await asyncio.to_thread(sms_outbox.project, org_id, identity[0])
                if msg_type == "sms.result" and status is not None:
                    await websocket.send_json({"type": "sms.result.ack", "id": msg.get("id"),
                                               "status": status, "applied": applied})
            elif msg_type == "sms.received":
                from_number = msg.get("from", "")
                text = msg.get("text", "")
                if from_number:
                    inbox_id = await asyncio.to_thread(record_inbound_sms, org_id, token_id, from_number, text)
                    asyncio.create_task(process_inbound_sms(inbox_id))
                ack_id = msg.get("id")
                if ack_id:
                    await websocket.send_text(json.dumps({"type": "sms.received.ack", "id": ack_id}))
            else:
                logger.debug("SMS-Gateway unbekannter Typ: %s", msg_type)

    except WebSocketDisconnect as exc:
        logger.info(
            "SMS-Gateway getrennt (org_id=%s, token_id=%s, code=%s, reason=%s, duration_s=%.1f)",
            org_id, token_id, exc.code, getattr(exc, "reason", ""), time.monotonic() - connected_at,
        )
    except Exception as exc:
        logger.warning(
            "SMS-Gateway Fehler (org_id=%s, token_id=%s, duration_s=%.1f, %s): %s",
            org_id, token_id, time.monotonic() - connected_at, type(exc).__name__, exc,
        )
        raise
    finally:
        with anyio.CancelScope(shield=True):
            _discard_gateway(org_id, websocket)
            gateway_routing.detach(connection)
            await asyncio.to_thread(gateway_routing.end, connection)


def is_sms_gateway_connected(org_id: int) -> bool:
    """Gibt True zurück wenn mindestens ein SMS-Gateway für diese Org verbunden ist."""
    return bool(gateway_routing.targets("sms", org_id))


def _discard_gateway(org_id: int, websocket: WebSocket) -> None:
    """Entfernt eine Gateway-Verbindung aus der Registry (idempotent)."""
    gateways = _sms_gateways.get(org_id, [])
    for entry in list(gateways):
        if entry[1] is websocket:
            gateways.remove(entry)
    if not gateways:
        _sms_last_gateway.pop(org_id, None)


_DEFAULT_GATEWAY_PRIORITY = 100


def _load_gateway_priorities(org_id: int) -> dict[int, int]:
    """Liefert token_id -> priority aller SMS-Gateway-Tokens dieser Organisation.

    Fehlt eine token_id (z. B. geloescht oder Test ohne DB-Zeile), gilt die
    Default-Prioritaet. Damit bleibt bei Gleichstand neueste Verbindung zuerst.
    """
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(SmsGatewayToken.id, SmsGatewayToken.priority).filter(
            SmsGatewayToken.org_id == org_id
        ).all()
        return {token_id: priority for token_id, priority in rows}
    finally:
        db.close()


def connected_gateway_token_ids(org_id: int) -> set[int]:
    """Token-IDs aller aktuell verbundenen SMS-Gateways fuer die Admin-Ansicht."""
    return {c.gateway_id for c in gateway_routing.targets("sms", org_id)}


def _ordered_sms_gateway_entries(org_id: int) -> list[tuple[int, WebSocket]]:
    """Ein Platz je Geraet; bei Reconnect nur dessen neuesten Socket verwenden."""
    newest: dict[int, WebSocket] = {}
    for token_id, websocket in reversed(_sms_gateways.get(org_id, [])):
        newest.setdefault(token_id, websocket)
    if not newest:
        return []
    entries = list(newest.items())
    priorities = _load_gateway_priorities(org_id)
    entries.sort(key=lambda entry: priorities.get(entry[0], _DEFAULT_GATEWAY_PRIORITY))
    return entries


def connected_gateway_token_ids_ordered(org_id: int) -> list[int]:
    """Verbundene Gateways in stabiler Prioritaetsreihenfolge fuer Batch-Verteilung."""
    priorities = _load_gateway_priorities(org_id)
    return sorted(connected_gateway_token_ids(org_id), key=lambda tid: (priorities.get(tid, 100), -tid))


async def dispatch_sms(org_id: int, job_id: str, to: str, text: str, timeout: float = 15.0,
                       preferred_gateway_token_id: int | None = None) -> dict:
    """Kompatible Gateway-API, Geschaeftszustand und Generation liegen in der SMS-Outbox."""
    from app.services.sms_service import SmsContext, send_sms
    if not is_sms_gateway_connected(org_id):
        from app.services import sms_outbox
        await asyncio.to_thread(sms_outbox.ensure, org_id, job_id, to, text, ["gateway"], preferred_gateway_token_id)
        raise RuntimeError(f"Kein SMS-Gateway fuer org_id={org_id} verbunden")
    result = await send_sms(org_id, to, text, timeout=timeout, ctx=SmsContext(org_id, ["gateway"], None),
                            job_id=job_id, preferred_gateway_token_id=preferred_gateway_token_id)
    if result.status not in ("sent", "failed"):
        raise RuntimeError(f"SMS-Gateway Timeout oder unklarer Versand fuer Job {job_id}")
    return {"ok": bool(result), "status": result.status, "job_id": result.job_id,
            "gateway_token_id": result.gateway_token_id, "error": result.error}


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
            .filter(Gateway.device_token_hash == token_hash, Gateway.org_id.isnot(None))
            .first()
        )
        if gw is not None:
            _ = gw.org_id, gw.id, gw.kind  # eager vor Session-Close
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


def _apply_job_status(job_id: int, status: str, error: str | None, *,
                      org_id: int | None = None, gateway_id: int | None = None,
                      connection: gateway_presence.Connection | None = None,
                      token_hash: str | None = None) -> int | None:
    """Schreibt job_status vom Gateway in die DB.

    Gibt org_id zurueck, wenn der Statusuebergang tatsaechlich vollzogen wurde, sonst
    None (unbekannter Status, Job weg, oder Job bereits in einem Terminal-Status).
    """
    from app.models.gateway import JOB_STATUS_LABELS, JOB_TERMINAL, PrintJob

    if connection is not None and (connection.kind != "linux" or connection.org_id != org_id
                                   or connection.gateway_id != gateway_id):
        return None
    if status not in JOB_STATUS_LABELS:
        logger.warning("Unbekannter Druckauftrag-Status %s fuer Job %s ignoriert", status, job_id)
        return None

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        if connection is not None and gateway_presence.state(
            db, "linux", connection.org_id, connection.gateway_id, token_hash or "",
            connection.connection_id, lock=True,
        ) != "connected":
            return None
        job = db.get(PrintJob, job_id)
        if job is None or job.status in JOB_TERMINAL:
            return None
        if org_id is not None and (job.org_id != org_id or job.gateway_id != gateway_id):
            return None
        expected = job.status
        if expected == "printing" and status == "sent":
            return None
        values: dict[Any, Any] = {"status": status}
        if error:
            values["error"] = error[:500]
        changed = db.query(PrintJob).filter(PrintJob.id == job_id, PrintJob.org_id == job.org_id,
            PrintJob.gateway_id == job.gateway_id, PrintJob.status == expected
        ).update(values, synchronize_session=False)
        db.commit()
        return job.org_id if changed else None
    finally:
        db.close()


def _log_gateway_job_status(
    job_id: object, status: object, org_id: int, gateway_id: int, error: object,
) -> None:
    logger.info(
        "ECPG job_status (job_id=%s, status=%s, org_id=%s, gateway_id=%s, error=%s)",
        job_id, status, org_id, gateway_id, error,
    )


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
    gateway = await asyncio.to_thread(_resolve_gateway, websocket)
    # Windows-Druckagents sprechen das Lease-Protokoll auf /ws/print-agent und dürfen die
    # Org-weiten print_job-Broadcasts dieses Kanals nie erhalten.
    if gateway is None or gateway.is_windows_agent:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    org_id = gateway.org_id
    gateway_id = gateway.id

    connection = await asyncio.to_thread(gateway_routing.begin, "linux", org_id, gateway_id, gateway.device_token_hash)
    if connection is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    try:
        await websocket.accept()
    except Exception:
        await asyncio.to_thread(gateway_routing.end, connection)
        raise
    gateway_routing.attach(connection, gateway.device_token_hash, websocket)
    _print_gateways[org_id].append((gateway_id, websocket))
    try:
        await broadcast_org(org_id, {"type": "gateway_status", "gateway_id": gateway_id, "online": True})
        logger.info("ECPG-Gateway verbunden (org_id=%s, gateway_id=%s, connection_id=%s, worker_id=%s)",
                    org_id, gateway_id, connection.connection_id, connection.worker_id)

        # config_sync sofort pushen
        await websocket.send_text(json.dumps({
            "type": "config_sync", "payload": await asyncio.to_thread(_gateway_config_sync, gateway_id),
        }, ensure_ascii=False))
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except TimeoutError:
                if await asyncio.to_thread(gateway_routing.state, connection, gateway.device_token_hash) == "connected":
                    continue
                await websocket.close(code=4401)
                break
            if await asyncio.to_thread(gateway_routing.state, connection, gateway.device_token_hash) != "connected":
                await websocket.close(code=4401)
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type")
            if mtype == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif mtype in ("pong", "ack"):
                pass
            elif mtype == "log_event":
                p = msg.get("payload") or {}
                level = str(p.get("level") or "info").lower()
                log_fn = logger.error if level == "error" else logger.warning if level == "warning" else logger.info
                log_fn("ECPG-Gateway %s: %s", gateway_id, p.get("message", ""))
            elif mtype == "hello":
                await asyncio.to_thread(gateway_routing.touch, connection, gateway.device_token_hash)
                await asyncio.to_thread(_linux_update, connection, gateway.device_token_hash,
                                        "version", (msg.get("payload") or {}).get("version"))
                await websocket.send_text(json.dumps({
                    "type": "config_sync", "payload": await asyncio.to_thread(_gateway_config_sync, gateway_id),
                }, ensure_ascii=False))
            elif mtype == "heartbeat":
                await asyncio.to_thread(gateway_routing.touch, connection, gateway.device_token_hash)
            elif mtype == "job_status":
                p = msg.get("payload") or {}
                jid = p.get("job_id")
                from app.models.gateway import JOB_STATUS_LABELS
                if p.get("status") not in JOB_STATUS_LABELS:
                    logger.warning("Ungueltige Gateway-Statusmeldung ignoriert (gateway_id=%s)", gateway_id)
                    continue
                if isinstance(jid, str) and jid.isascii() and jid.isdecimal():
                    jid = int(jid)
                if isinstance(jid, int) and await asyncio.to_thread(_print_target, org_id, jid, gateway_id) is not None:
                    o = await asyncio.to_thread(_apply_job_status, jid, p.get("status", ""), p.get("error"),
                                                org_id=org_id, gateway_id=gateway_id, connection=connection,
                                                token_hash=gateway.device_token_hash)
                    await asyncio.to_thread(gateway_routing.record_result, connection,
                                            gateway.device_token_hash, str(jid), p)
                    _log_gateway_job_status(
                        jid, p.get("status"), org_id, gateway_id, p.get("error"),
                    )
                    if o:
                        await broadcast_org(o, {"type": "print_job_status", "job_id": int(jid),
                                                "status": p.get("status")})
                        # Erst nach dem Broadcast: der Ersatzdruck wartet auf die
                        # Zustellung (bis 20 s Timeout) und darf die Statusmeldung
                        # an die Clients nicht so lange aufhalten.
                        from app.models.gateway import JOB_FAILED
                        if p.get("status") == JOB_FAILED:
                            from app.services.print_dispatcher import dispatch_fallback_for_failed_job
                            await dispatch_fallback_for_failed_job(int(jid))
            elif mtype == "serial_status":
                p = msg.get("payload") or {}
                changed = await asyncio.to_thread(_linux_update, connection, gateway.device_token_hash,
                                                  "serial_connected", bool(p.get("connected")))
                o = org_id if changed else None
                if o:
                    await broadcast_org(o, {"type": "gateway_serial", "gateway_id": gateway_id,
                                            "connected": bool(p.get("connected"))})
            elif mtype == "printer_report":
                from app.services.printer_report_service import apply_printer_report
                await asyncio.to_thread(_linux_report, connection, gateway.device_token_hash,
                                        apply_printer_report, msg.get("payload") or {})
                await broadcast_org(org_id, {"type": "printer_report", "gateway_id": gateway_id})
            elif mtype == "printer_status":
                # Periodischer Erreichbarkeits-Check je Drucker (id-basiert).
                from app.services.printer_report_service import apply_printer_status
                await asyncio.to_thread(_linux_report, connection, gateway.device_token_hash,
                                        apply_printer_status, msg.get("payload") or {})
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
        with anyio.CancelScope(shield=True):
            _discard_print_gateway(org_id, websocket)
            open_jobs = _count_open_print_jobs(gateway_id)
            if open_jobs:
                logger.warning(
                    "ECPG-Gateway getrennt mit %d offenen Druckaufträgen "
                    "(org_id=%s, gateway_id=%s)", open_jobs, org_id, gateway_id,
                )
            gateway_routing.detach(connection)
            if await asyncio.to_thread(gateway_routing.end, connection):
                await broadcast_org(org_id, {"type": "gateway_status", "gateway_id": gateway_id, "online": False})


def _discard_print_gateway(org_id: int, websocket: WebSocket) -> None:
    gateways = _print_gateways.get(org_id, [])
    for entry in list(gateways):
        if entry[1] is websocket:
            gateways.remove(entry)


def _mark_gateway_offline(gateway_id: int) -> None:
    from app.models.gateway import GATEWAY_STATUS_OFFLINE, Gateway

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = db.get(Gateway, gateway_id)
        # Nur offline setzen wenn dieses konkrete Gateway keine weitere offene
        # Verbindung an diesem Worker mehr hat (nicht: die Org als Ganzes).
        if (
            gw
            and gw.org_id is not None
            and gw.agent_connection_id is None
            and gateway_id not in connected_print_gateway_ids(gw.org_id)
        ):
            gw.status = GATEWAY_STATUS_OFFLINE
            gw.serial_connected = False
            db.commit()
    finally:
        db.close()


def _count_open_print_jobs(gateway_id: int) -> int:
    from app.models.gateway import JOB_PRINTING, JOB_SENT, PrintJob

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return (
            db.query(PrintJob)
            .filter(PrintJob.gateway_id == gateway_id, PrintJob.status.in_((JOB_SENT, JOB_PRINTING)))
            .execution_options(include_all_tenants=True)
            .count()
        )
    finally:
        db.close()


def is_gateway_connected(org_id: int) -> bool:
    """True, wenn ein ECPG-Gateway dieser Org an DIESEM Worker verbunden ist."""
    return bool(_print_gateways.get(org_id))


def connected_print_gateway_ids(org_id: int) -> set[int]:
    """Gateway-IDs aller aktuell an DIESEM Worker verbundenen Print-Gateways."""
    return {gateway_id for gateway_id, _ in _print_gateways.get(org_id, [])}


def gateway_online(org_id: int | None) -> bool:
    return org_id is not None and bool(gateway_routing.targets("linux", org_id))


def gateway_online_id(gateway_id: int, org_id: int | None) -> bool:
    if org_id is None:
        return False
    return any(gateway_routing.targets(kind, org_id, gateway_id) for kind in ("linux", "windows_agent"))


def _linux_update(connection, token_hash, field: str, value) -> bool:
    from app.models.gateway import Gateway
    with SessionLocal() as db:
        set_tenant_context(db, None)
        if gateway_presence.state(db, "linux", connection.org_id, connection.gateway_id,
                                  token_hash, connection.connection_id) != "connected":
            return False
        changed = db.query(Gateway).filter(Gateway.org_id == connection.org_id,
            Gateway.id == connection.gateway_id, Gateway.kind == "linux",
            Gateway.agent_presence_expires_at > gateway_presence.utc(),
            Gateway.agent_connection_token_hash == token_hash,
            Gateway.agent_connection_id == connection.connection_id,
            Gateway.device_token_hash == token_hash).update({field: value}, synchronize_session=False)
        db.commit()
        return bool(changed)


def _linux_report(connection, token_hash, fn, payload):
    from app.models.gateway import Gateway
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.query(Gateway).filter(Gateway.org_id == connection.org_id,
            Gateway.id == connection.gateway_id).with_for_update().first()
        if gateway_presence.state(db, "linux", connection.org_id, connection.gateway_id,
                                  token_hash, connection.connection_id) == "connected":
            fn(connection.gateway_id, connection.org_id, payload)


def _print_target(org_id: int, job_id: int, gateway_id: int | None = None):
    from app.models.gateway import PrintJob
    with SessionLocal() as db:
        set_tenant_context(db, None)
        query = db.query(PrintJob).filter(PrintJob.id == job_id, PrintJob.org_id == org_id)
        if gateway_id is not None:
            query = query.filter(PrintJob.gateway_id == gateway_id)
        job = query.first()
        return job.gateway_id if job is not None else None


async def _bus_gateway_deliver(payload: dict) -> None:
    if payload.get("worker_id") not in (None, gateway_presence.worker_id()):
        return
    if payload.get("kind") == "route" and isinstance(payload.get("message_id"), str):
        # Persistierte Zieladresse und atomarer Claim sind autoritativ, nie der Bus-Payload.
        await gateway_routing.deliver(payload["message_id"], address=payload)


ws_bus.register(ws_bus.CH_GW, _bus_gateway_deliver)


async def push_config_sync(org_id: int, gateway_id: int) -> None:
    for connection in await asyncio.to_thread(gateway_routing.targets, "linux", org_id, gateway_id):
        config = await asyncio.to_thread(_gateway_config_sync, gateway_id)
        await gateway_routing.send(connection, {"type": "config_sync", "payload": config})


def _command_gateway(org_id: int, message: dict) -> int | None:
    from app.models.gateway import Printer
    p = message.get("payload") or message
    if isinstance(p.get("gateway_id"), int):
        return p["gateway_id"]
    if isinstance(p.get("job_id"), int):
        return _print_target(org_id, p["job_id"]) or -1
    if isinstance(p.get("printer_id"), int):
        with SessionLocal() as db:
            set_tenant_context(db, None)
            printer = db.query(Printer).filter(Printer.org_id == org_id, Printer.id == p["printer_id"]).first()
            return printer.gateway_id if printer else -1
    return None if message.get("type") in ("discover_printers", "update_available") else -1


async def push_gateway_command(org_id: int, message: dict) -> bool:
    target = await asyncio.to_thread(_command_gateway, org_id, message)
    connections = await asyncio.to_thread(gateway_routing.targets, "linux", org_id, target)
    for connection in connections:
        await gateway_routing.send(connection, message)
    return bool(connections)


async def dispatch_print_job(org_id: int, job_id: int, payload: dict, timeout: float = 20.0) -> dict:
    gateway_id = await asyncio.to_thread(_print_target, org_id, job_id)
    connections = await asyncio.to_thread(gateway_routing.targets, "linux", org_id, gateway_id) if gateway_id else []
    if not connections:
        raise RuntimeError(f"Kein Gateway fuer Druckauftrag {job_id} (org_id={org_id}) verbunden")
    connection = connections[0]
    item_id = await gateway_routing.send(connection,
        {"type": "print_job", "id": str(job_id), "payload": payload}, ttl=timeout, job_key=str(job_id))
    try:
        return await gateway_routing.wait_result(item_id, org_id, timeout)
    except TimeoutError:
        return {"job_id": job_id, "status": "sent", "note": "timeout_waiting_status"}


async def _dispatch_print_job_local(org_id: int, job_id: int, payload: dict, timeout: float) -> dict:
    return await dispatch_print_job(org_id, job_id, payload, timeout)


# ── Windows-Druckagent (Lease-Protokoll) ───────────────────────────────────────
# Spezifikation: docs/print-agent-architektur.md. Anders als /ws/gateway wird hier nichts
# gepusht: der Agent holt sich Jobs per lease_request, die Cloud entscheidet atomar in der DB.
# Gezielte Hinweise (job_available, cancel_job, config_sync) laufen über den Bus-Kind
# "agent" an genau die adressierten Agents – egal, an welchem Worker deren Socket hängt.

def _agent_call(gateway_id: int, fn, *args, connection_id: str | None = None, token_hash: str | None = None):
    """Führt eine print_agent_service-Funktion mit frisch geladenem Agent-Gateway aus."""
    from app.models.gateway import Gateway

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = db.query(Gateway).filter(Gateway.id == gateway_id).with_for_update().first()
        if gw is None or not gw.is_windows_agent or not gw.device_token_hash:
            return None
        if connection_id is not None:
            if gw.org_id is None or token_hash is None:
                return None
            from app.services import print_agent_presence
            if print_agent_presence.state(db, gw.org_id, gateway_id, token_hash, connection_id) != "connected":
                return None
        return fn(db, gw, *args)
    finally:
        db.close()


def _agent_hello(db, gw, payload: dict) -> list[dict]:
    from app.services import print_agent_service
    from app.services.gateway_service import mark_seen

    mark_seen(db, gw, version=payload.get("version"))
    info = dict(gw.agent_info or {})
    host_info = payload.get("host_info")
    if isinstance(host_info, dict):
        info["host"] = host_info
    info["version"] = payload.get("version")
    gw.agent_info = info
    db.commit()
    open_jobs = [eintrag for eintrag in (payload.get("open_jobs") or []) if isinstance(eintrag, dict)]
    return print_agent_service.resume(db, gw, open_jobs)


def _agent_touch(db, gw) -> None:
    from app.services.gateway_service import mark_seen

    mark_seen(db, gw)
    db.commit()


def _discard_print_agent(org_id: int, websocket: WebSocket) -> None:
    _print_agent_sessions.pop(websocket, None)
    agents = _print_agents.get(org_id, [])
    for entry in list(agents):
        if entry[1] is websocket:
            agents.remove(entry)


def connected_print_agent_ids(org_id: int) -> set[int]:
    """Gateway-IDs aller an DIESEM Worker verbundenen Windows-Druckagents."""
    return {gateway_id for gateway_id, _ in _print_agents.get(org_id, [])}


def _mark_agent_offline(org_id: int, gateway_id: int, connection_id: str) -> bool:
    from app.services.print_agent_presence import end
    with SessionLocal() as db:
        set_tenant_context(db, None)
        return end(db, org_id, gateway_id, connection_id)


async def _send_to_local_agents(org_id: int, gateway_ids: set, message: dict) -> bool:
    """Stellt eine Nachricht an die an DIESEM Worker verbundenen, adressierten Agents zu."""
    payload = json.dumps(message, ensure_ascii=False)
    sent = False
    for gateway_id, ws in list(_print_agents.get(org_id, [])):
        if gateway_id not in gateway_ids:
            continue
        session = _print_agent_sessions.get(ws)
        if session is not None:
            from app.services import print_agent_presence
            with SessionLocal() as db:
                set_tenant_context(db, None)
                state = print_agent_presence.state(db, session[0], session[1], session[3], session[2])
            if state != "connected":
                continue
        try:
            await ws.send_text(payload)
            sent = True
        except Exception:
            _discard_print_agent(org_id, ws)
    return sent


async def push_agent_message(org_id: int, gateway_ids: list[int], message: dict) -> None:
    for gateway_id in set(gateway_ids):
        for connection in await asyncio.to_thread(gateway_routing.targets, "windows_agent", org_id, gateway_id):
            await gateway_routing.send(connection, message)


async def notify_agents_job_available(org_id: int, gateway_ids: list[int], job_ids: list[int]) -> None:
    await push_agent_message(org_id, gateway_ids, {"type": "job_available", "payload": {"job_ids": job_ids}})


async def _agent_job_status(websocket: WebSocket, org_id: int, gateway_id: int, msg: dict,
                            *, connection_id: str | None = None, token_hash: str | None = None) -> None:
    from app.models.gateway import JOB_SENT
    from app.services import print_agent_service

    p = msg.get("payload") or {}
    jid = p.get("job_id")
    ergebnis = None
    if isinstance(jid, int):
        ergebnis = await asyncio.to_thread(
            _agent_call,
            gateway_id, print_agent_service.apply_agent_status, jid, str(p.get("lease_token") or ""),
            str(p.get("status") or ""), p.get("phase"), p.get("spool_ref"), p.get("error"),
            connection_id=connection_id, token_hash=token_hash,
        )
        _log_gateway_job_status(jid, p.get("status"), org_id, gateway_id, p.get("error"))
    # Ack auch bei veralteten Meldungen: die Outbox des Agents soll sie nicht endlos wiederholen.
    if msg.get("id") is not None:
        await websocket.send_text(json.dumps({"type": "ack", "payload": {"id": msg.get("id")}}))
    if not ergebnis or ergebnis.get("result") != "applied" or not isinstance(jid, int):
        return
    status = JOB_SENT if ergebnis.get("requeued") else ergebnis.get("status")
    await broadcast_org(org_id, {"type": "print_job_status", "job_id": jid, "status": status})
    if ergebnis.get("requeued"):
        await notify_agents_job_available(org_id, ergebnis.get("notify_gateway_ids") or [], [jid])
    if ergebnis.get("fallback"):
        from app.services.print_dispatcher import dispatch_fallback_for_failed_job
        await dispatch_fallback_for_failed_job(jid)


@router.websocket("/ws/print-agent")
async def print_agent_ws(websocket: WebSocket):
    """WebSocket-Kanal für Windows-Druckagents (ausgehend vom Agent, Lease-Protokoll).

    Auth per Bearer-Device-Token wie /ws/gateway; nur Gateways vom Typ windows_agent.
    """
    from uuid import uuid4

    from app.services import print_agent_presence, print_agent_service

    gateway = await asyncio.to_thread(_resolve_gateway, websocket)
    if gateway is None or not gateway.is_windows_agent:
        logger.warning("agent_authentication_failed at=%s", datetime.now(UTC).isoformat())
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    org_id = gateway.org_id
    gateway_id = gateway.id

    connection_id = uuid4().hex
    token_hash = gateway.device_token_hash
    def begin_connection():
        with SessionLocal() as db:
            set_tenant_context(db, None)
            return print_agent_presence.begin(db, org_id, gateway_id, token_hash, connection_id)

    if not await asyncio.to_thread(begin_connection):
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    async def agent_call(fn, *args):
        return await asyncio.to_thread(
            _agent_call, gateway_id, fn, *args, connection_id=connection_id, token_hash=token_hash,
        )

    def connection_state():
        with SessionLocal() as db:
            set_tenant_context(db, None)
            return print_agent_presence.state(db, org_id, gateway_id, token_hash, connection_id)

    def touch(db, gw, heartbeat=False):
        return print_agent_presence.touch(db, gw, connection_id, token_hash, heartbeat=heartbeat)

    try:
        await websocket.accept()
    except Exception as exc:
        logger.warning("agent_accept_failed org_id=%s gateway_id=%s exception=%s: %s",
                       org_id, gateway_id, type(exc).__name__, exc)
        await asyncio.to_thread(_mark_agent_offline, org_id, gateway_id, connection_id)
        raise
    connected_at = time.monotonic()
    _print_agents[org_id].append((gateway_id, websocket))
    _print_agent_sessions[websocket] = (org_id, gateway_id, connection_id, token_hash)
    connections = await asyncio.to_thread(gateway_routing.targets, "windows_agent", org_id, gateway_id)
    connection = next((c for c in connections if c.connection_id == connection_id), None)
    if connection is not None:
        gateway_routing.attach(connection, token_hash, websocket)
    logger.info("agent_authentication_succeeded org_id=%s gateway_id=%s connection_id=%s at=%s",
                org_id, gateway_id, connection_id, datetime.now(UTC).isoformat())

    async def senden(mtype: str, payload: dict, rid: object = None) -> None:
        nachricht: dict = {"type": mtype, "payload": payload}
        if rid is not None:
            nachricht["rid"] = rid
        await websocket.send_text(json.dumps(nachricht, ensure_ascii=False, default=str))

    async def config_senden() -> None:
        await senden("config_sync", await agent_call(print_agent_service.agent_config_sync) or {})

    try:
        await broadcast_org(org_id, {"type": "gateway_status", "gateway_id": gateway_id, "online": True})
        await config_senden()
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except TimeoutError:
                reason = await asyncio.to_thread(connection_state)
                if reason == "connected":
                    continue
                logger.warning("agent_%s org_id=%s gateway_id=%s connection_id=%s at=%s",
                               reason, org_id, gateway_id, connection_id, datetime.now(UTC).isoformat())
                await websocket.close(code=4408 if reason == "heartbeat_overdue" else 4401)
                break
            reason = await asyncio.to_thread(connection_state)
            if reason != "connected":
                logger.warning("agent_%s org_id=%s gateway_id=%s connection_id=%s at=%s",
                               reason, org_id, gateway_id, connection_id, datetime.now(UTC).isoformat())
                await websocket.close(code=4401 if reason == "authentication_failed" else 4409)
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type")
            roh = msg.get("payload")
            p: dict = roh if isinstance(roh, dict) else {}
            rid = msg.get("rid")
            if mtype == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif mtype in ("pong", "ack"):
                pass
            elif mtype == "heartbeat":
                await agent_call(touch, True)
            elif mtype == "hello":
                await agent_call(touch)
                logger.info("agent_hello org_id=%s gateway_id=%s connection_id=%s at=%s",
                            org_id, gateway_id, connection_id, datetime.now(UTC).isoformat())
                abandon = await agent_call(_agent_hello, p) or []
                await config_senden()
                for eintrag in abandon:
                    await senden("abandon", eintrag)
            elif mtype == "inventory":
                await agent_call(print_agent_service.inventar_speichern, p)
                await broadcast_org(org_id, {"type": "printer_report", "gateway_id": gateway_id})
            elif mtype == "readiness":
                routen = [eintrag for eintrag in (p.get("routes") or []) if isinstance(eintrag, dict)]
                await agent_call(print_agent_service.route_readiness_aktualisieren, routen)
                await broadcast_org(org_id, {"type": "printer_report", "gateway_id": gateway_id})
            elif mtype == "lease_request":
                try:
                    anzahl = max(0, min(int(p.get("max") or 1), 10))
                except (TypeError, ValueError):
                    anzahl = 1
                grants = await agent_call(print_agent_service.lease_jobs, anzahl) or []
                await senden("lease_grant", {"jobs": grants}, rid)
                for grant in grants:
                    await broadcast_org(org_id, {"type": "print_job_status", "job_id": grant["job_id"],
                                                 "status": "sent"})
            elif mtype == "lease_renew":
                leases = [eintrag for eintrag in (p.get("leases") or []) if isinstance(eintrag, dict)]
                ungueltig = await agent_call(print_agent_service.renew, leases) or []
                for job_id in ungueltig:
                    await senden("abandon", {"job_id": job_id, "reason": "invalid_lease"})
            elif mtype == "print_intent":
                jid = p.get("job_id")
                # v2026.10.23.1 sendet nur beim Intent noch String-IDs.
                # Dezimale BigInteger-IDs akzeptieren; Token-/Eigentumsprüfung bleibt unverändert.
                if isinstance(jid, str) and jid.isascii() and jid.isdecimal() and len(jid) <= 19:
                    numeric_id = int(jid)
                    if 0 < numeric_id <= 9223372036854775807:
                        jid = numeric_id
                ok, grund = False, "unknown"
                if isinstance(jid, int):
                    antwort = await agent_call(print_agent_service.print_intent, jid,
                                          str(p.get("lease_token") or ""))
                    if antwort is not None:
                        ok, grund = antwort
                await senden("intent_result", {"job_id": jid, "ok": ok, "reason": grund}, rid)
            elif mtype == "job_status":
                await _agent_job_status(websocket, org_id, gateway_id, msg,
                                        connection_id=connection_id, token_hash=token_hash)
            elif mtype == "log_event":
                level = str(p.get("level") or "info").lower()
                log_fn = logger.error if level == "error" else logger.warning if level == "warning" else logger.info
                log_fn("Windows-Druckagent %s: %s", gateway_id, str(p.get("message", ""))[:1000])
            else:
                logger.debug("Windows-Druckagent unbekannter Typ: %s", mtype)
    except WebSocketDisconnect as exc:
        logger.info("agent_disconnected org_id=%s gateway_id=%s connection_id=%s at=%s "
                    "code=%s reason=%s duration_s=%.1f",
                    org_id, gateway_id, connection_id, datetime.now(UTC).isoformat(),
                    exc.code, getattr(exc, "reason", ""), time.monotonic() - connected_at)
    except Exception as exc:
        logger.warning("agent_error org_id=%s gateway_id=%s connection_id=%s duration_s=%.1f exception=%s: %s",
                       org_id, gateway_id, connection_id, time.monotonic() - connected_at, type(exc).__name__, exc)
        raise
    finally:
        with anyio.CancelScope(shield=True):
            _discard_print_agent(org_id, websocket)
            if connection is not None:
                gateway_routing.detach(connection)
            if await asyncio.to_thread(_mark_agent_offline, org_id, gateway_id, connection_id):
                await broadcast_org(org_id, {"type": "gateway_status", "gateway_id": gateway_id, "online": False})
