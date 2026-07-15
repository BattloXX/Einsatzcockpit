"""Lagedokument: gemeinsam bearbeitbares Dokument je Lage (GSL/Stab).

Nutzerseitig heisst das Feature "Lagebericht" (Nav-Label, Seitentitel in
lagedokument.html) -- intern heissen Modell/Tabelle/Router weiter
"LageDokument"/"lage_dokument"/"lagedokument" (historisch entstanden, um eine
Routen-/Namenskollision mit der urspruenglich EPHEMEREN KI-Zusammenfassung
(POST /lage/{id}/lagebericht, ui_major_incident.py::lage_ki_bericht) zu
vermeiden). Diese beiden Werkzeuge sind inzwischen zusammengefuehrt: der
"KI-Entwurf"-Button in lagedokument.html ruft dieselbe (unveraendert
gebliebene) Route auf und fuegt den generierten Text in den Editor ein, statt
ihn nur ephemer anzuzeigen. Eigenstaendig vom Einsatzjournal (das bleibt
Append-only). Ein dauerhaftes, fortlaufend bearbeitbares Textdokument je Lage,
gedacht als zusammenfassende Lagedarstellung (SKKM-Lagemeldung,
Uebergabeprotokoll o.Ae.).

PR 1: klassisches Speichern (kein Realtime-Sync).
PR 2: WebSocket-Endpoint fuer die Yjs-CRDT-Live-Kollaboration (Sync-Relay ueber
app/services/lagedokument_collab.py).
PR 5: Mehr-Worker-Korrektheit ueber den Redis-Bus (app/services/ws_bus.py) --
lagedokument_collab wird deshalb HIER auf Modulebene importiert (nicht erst
lazy im WS-Handler), damit dessen ws_bus.register(...)-Aufrufe VOR
ws_bus.start() (main.py, nach Router-Import) laufen und die Kanaele
abonniert werden.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from pycrdt import Channel
from sqlalchemy.orm import Session

from app.core.permissions import has_role, require_role, same_org_or_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.major_incident import LageDokument, MajorIncident
from app.services.lagedokument_collab import get_or_create_room, release_room_if_empty

router = APIRouter()
logger = logging.getLogger("einsatzleiter.lagedokument")

_EDIT_ROLLEN = ("incident_leader", "admin", "org_admin", "recorder")
_LESE_ROLLEN = (*_EDIT_ROLLEN, "readonly")

WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403


def _lage_or_404(lage_id: int, db: Session) -> MajorIncident:
    lage = db.get(MajorIncident, lage_id)
    if not lage:
        raise HTTPException(status_code=404, detail="Lage nicht gefunden")
    return lage


def _check_org_access(user, lage: MajorIncident) -> None:
    if not same_org_or_system_admin(user, lage.org_id):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Lage")


def _get_or_create_dokument(db: Session, lage: MajorIncident) -> LageDokument:
    dokument = db.query(LageDokument).filter(LageDokument.major_incident_id == lage.id).first()
    if dokument is None:
        dokument = LageDokument(major_incident_id=lage.id, org_id=lage.org_id,
                                updated_at=datetime.now(UTC))
        db.add(dokument)
        db.flush()
    return dokument


@router.get("/lage/{lage_id}/lagedokument", response_class=HTMLResponse)
async def lagedokument_view(
    lage_id: int,
    request: Request,
    db: Session = Depends(get_db),
    gespeichert: int = 0,
    _=Depends(require_role(*_LESE_ROLLEN)),
):
    from app.routers.ui_major_incident import _get_mi_features

    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    dokument = _get_or_create_dokument(db, lage)
    db.commit()
    return templates.TemplateResponse(request, "incident_major/lagedokument.html", {
        "user": user,
        "lage": lage,
        "dokument": dokument,
        "can_edit": has_role(user, *_EDIT_ROLLEN),
        "gespeichert": bool(gespeichert),
        "mi_features": _get_mi_features(db, lage.org_id),
    })


@router.post("/lage/{lage_id}/lagedokument", response_class=HTMLResponse)
async def lagedokument_save(
    lage_id: int,
    request: Request,
    db: Session = Depends(get_db),
    content_html: str = Form(""),
    _=Depends(require_role(*_EDIT_ROLLEN)),
):
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    dokument = _get_or_create_dokument(db, lage)
    dokument.content_html = content_html  # Sanitizing laeuft im @validates-Hook des Modells
    dokument.updated_at = datetime.now(UTC)
    dokument.updated_by_user_id = user.id
    db.commit()
    return RedirectResponse(f"/lage/{lage_id}/lagedokument?gespeichert=1", status_code=303)


@router.get("/lage/{lage_id}/lagedokument/druck", response_class=HTMLResponse)
async def lagedokument_druck(
    lage_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(*_LESE_ROLLEN)),
):
    """Druckansicht auf Basis des letzten gespeicherten HTML-Snapshots (nicht des
    Live-Yjs-Zustands -- konsistent mit den uebrigen /druck-Routen: Browser-Print
    ueber window.print(), kein serverseitiges PDF-Rendering fuer dieses Dokument)."""
    user = request.state.user
    lage = _lage_or_404(lage_id, db)
    _check_org_access(user, lage)
    dokument = _get_or_create_dokument(db, lage)
    db.commit()
    return templates.TemplateResponse(request, "incident_major/_lagedokument_druck.html", {
        "lage": lage,
        "dokument": dokument,
        "now": datetime.now(UTC),
    })


# ── Realtime-Kollaboration (Yjs-CRDT-Sync) ────────────────────────────────────

class _FastAPIChannel(Channel):
    """Adapter: erfuellt pycrdt's `Channel`-Protokoll ueber eine FastAPI-WebSocket-Verbindung,
    damit YRoom.serve() sie direkt bedienen kann.

    Muss von `Channel` ERBEN (nicht nur strukturell path/send/recv nachbilden) --
    YRoom.serve() iteriert per `async for message in channel:`, was `__aiter__`/`__anext__`
    voraussetzt. `Channel` liefert dafuer eine Default-Implementierung, die aber nur bei
    tatsaechlicher Vererbung wirksam wird (Bugfix 2026-07-15: die vorherige eigenstaendige
    Klasse ohne Vererbung crashte bei jeder Verbindung mit
    "TypeError: 'async for' requires an object with __aiter__ method" direkt nach der
    ersten SYNC_STEP1-Nachricht -- die Live-Kollaboration hat dadurch nie echte Client-
    Aenderungen empfangen). Muster: pycrdt.websocket.websocket.HttpxWebsocket (eigene
    __anext__-Override wandelt Verbindungsabbrueche in ein sauberes StopAsyncIteration
    statt sie als Fehler im Sync-Room zu loggen)."""

    def __init__(self, websocket: WebSocket, path: str):
        self._ws = websocket
        self._path = path

    @property
    def path(self) -> str:
        return self._path

    async def send(self, message: bytes) -> None:
        await self._ws.send_bytes(message)

    async def recv(self) -> bytes:
        return await self._ws.receive_bytes()

    async def __anext__(self) -> bytes:
        try:
            return await self.recv()
        except Exception:
            raise StopAsyncIteration() from None


@router.websocket("/ws/lagedokument/{lage_id}")
async def lagedokument_ws(websocket: WebSocket, lage_id: int):
    """Realtime-Sync-Kanal fuer ein Lagedokument. Nur fuer Bearbeiten-Rollen --
    Lesende (readonly) bekommen im Template gar keine Editor-/Yjs-Anbindung."""
    from app.core.security import unsign_session
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.user import User

    token = websocket.cookies.get("session")
    session_data = unsign_session(token) if token else None
    if not session_data:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    user_id = session_data[0]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = db.query(User).filter(User.id == user_id, User.active == True).first()  # noqa: E712
        if user is None:
            await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
            return
        _ = [r.code for r in user.roles]  # vor Session-Ende laden
        if not has_role(user, *_EDIT_ROLLEN):
            await websocket.close(code=WS_CLOSE_FORBIDDEN)
            return
        lage = db.get(MajorIncident, lage_id)
        if lage is None or not same_org_or_system_admin(user, lage.org_id):
            await websocket.close(code=WS_CLOSE_FORBIDDEN)
            return
        org_id = lage.org_id
    finally:
        db.close()

    await websocket.accept()
    channel = _FastAPIChannel(websocket, path=f"lagedokument-{lage_id}")
    try:
        room = await get_or_create_room(lage_id, org_id)
        await room.serve(channel)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Lagedokument-WS %s: Fehler im Sync-Room", lage_id)
    finally:
        try:
            await release_room_if_empty(lage_id)
        except Exception:
            logger.exception("Lagedokument-WS %s: Aufraeumen fehlgeschlagen", lage_id)
