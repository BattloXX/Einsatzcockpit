"""WebSocket connection manager – pub/sub per incident, major incident, and org."""
import asyncio
import json
from collections import defaultdict

from fastapi import WebSocket

# Lage-Kanäle verwenden einen Offset um Kollision mit Einsatz-IDs zu vermeiden
LAGE_WS_OFFSET = 10_000_000
# Org-Kanäle für globale Org-Benachrichtigungen (neue Einsätze, Einladungen …)
ORG_WS_OFFSET = 20_000_000


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

    async def broadcast(self, incident_id: int, event: dict) -> None:
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

    async def broadcast_all(self, event: dict) -> None:
        """Broadcast to every connected client (e.g. new incident created)."""
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


manager = ConnectionManager()


async def broadcast_lage(lage_id: int, event: dict) -> None:
    await manager.broadcast(LAGE_WS_OFFSET + lage_id, event)


async def broadcast_org(org_id: int, event: dict) -> None:
    """Sendet ein Event an alle Clients im globalen Kanal dieser Org."""
    await manager.broadcast(ORG_WS_OFFSET + org_id, event)
