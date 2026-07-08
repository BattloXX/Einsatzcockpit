"""WebSocket connection manager – pub/sub per incident, major incident, and org.

Worker-übergreifend: Broadcasts laufen über den Redis-Bus (app.services.ws_bus),
falls REDIS_URL gesetzt ist. Dann publiziert ``broadcast`` nur auf Redis; die
tatsächliche Zustellung an die lokalen Sockets erledigt ausschließlich der Bus-
Handler (``_bus_deliver``) – auf jedem Worker, inkl. des Publishers. Ohne Redis
(Dev / -w 1) wird direkt lokal zugestellt (``_deliver_local``).
"""
import asyncio
import json
from collections import defaultdict

from fastapi import WebSocket

from app.services import ws_bus

# Lage-Kanäle verwenden einen Offset um Kollision mit Einsatz-IDs zu vermeiden
LAGE_WS_OFFSET = 10_000_000
# Org-Kanäle für globale Org-Benachrichtigungen (neue Einsätze, Einladungen …)
ORG_WS_OFFSET = 20_000_000
# Sentinel-Key für broadcast_all über den Bus
_ALL_KEY = -1


class ConnectionManager:
    def __init__(self):
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, incident_id: int, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections[incident_id].add(ws)

    async def disconnect(self, incident_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._connections[incident_id].discard(ws)

    async def _deliver_local(self, incident_id: int, event: dict) -> None:
        """Stellt ein Event an die lokalen Sockets dieses Workers zu."""
        payload = json.dumps(event, ensure_ascii=False, default=str)
        dead: set[WebSocket] = set()
        for ws in list(self._connections.get(incident_id, [])):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                self._connections[incident_id] -= dead

    async def _deliver_all_local(self, event: dict) -> None:
        """Stellt ein Event an ALLE lokalen Sockets dieses Workers zu."""
        payload = json.dumps(event, ensure_ascii=False, default=str)
        all_ws = {ws for conns in self._connections.values() for ws in conns}
        dead: set[WebSocket] = set()
        for ws in all_ws:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                for conns in self._connections.values():
                    conns -= dead

    async def broadcast(self, incident_id: int, event: dict) -> None:
        """Broadcast an einen Kanal – worker-übergreifend via Bus, sonst lokal."""
        if ws_bus.enabled():
            await ws_bus.publish(ws_bus.CH_WS, {"key": incident_id, "event": event})
        else:
            await self._deliver_local(incident_id, event)

    async def broadcast_all(self, event: dict) -> None:
        """Broadcast to every connected client (e.g. new incident created)."""
        if ws_bus.enabled():
            await ws_bus.publish(ws_bus.CH_WS, {"key": _ALL_KEY, "event": event})
        else:
            await self._deliver_all_local(event)


manager = ConnectionManager()


async def _bus_deliver(payload: dict) -> None:
    """Bus-Handler (CH_WS): stellt ein empfangenes Broadcast-Event lokal zu."""
    key = payload.get("key")
    event = payload.get("event") or {}
    if key == _ALL_KEY:
        await manager._deliver_all_local(event)
    elif key is not None:
        await manager._deliver_local(int(key), event)


# Beim Import registrieren, damit ws_bus.start() den Kanal abonniert.
ws_bus.register(ws_bus.CH_WS, _bus_deliver)


async def broadcast_lage(lage_id: int, event: dict) -> None:
    await manager.broadcast(LAGE_WS_OFFSET + lage_id, event)


async def broadcast_org(org_id: int, event: dict) -> None:
    """Sendet ein Event an alle Clients im globalen Kanal dieser Org."""
    await manager.broadcast(ORG_WS_OFFSET + org_id, event)
