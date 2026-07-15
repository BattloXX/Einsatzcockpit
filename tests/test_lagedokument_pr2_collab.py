"""Lagedokument PR2: Yjs-CRDT-Sync-Server (WebSocket-Realtime-Kollaboration).

Prueft Auth/Rollen-Schutz des WS-Endpoints sowie den Room-Lifecycle
(Laden/Speichern von ydoc_state) direkt gegen den Service, ohne echten
Browser-Client -- der Yjs-Sync-Protokoll-Bytestream selbst wird ueber
pycrdt erzeugt/geprueft (kein JS noetig).
"""
import asyncio

import pytest
from pycrdt import Doc, Text

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageDokument, MajorIncident
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from app.services import lagedokument_collab as collab


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _make_user_with_lage(username: str, *, org_slug: str, rolle: str | None) -> tuple[int, int]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=f"Org {org_slug}", color="#778899", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Testuser", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        if rolle:
            role = db.query(Role).filter(Role.code == rolle).first()
            db.add(UserRole(user_id=user.id, role_id=role.id))
        lage = MajorIncident(org_id=org.id, name="Testlage")
        db.add(lage)
        db.commit()
        return org.id, lage.id
    finally:
        db.close()


def test_ws_ohne_login_wird_geschlossen(client, setup_db):
    _, lage_id = _make_user_with_lage("ldws_anon", org_slug="ldws-anon", rolle="incident_leader")
    with pytest.raises(Exception):  # noqa: B017 - Starlette wirft WebSocketDisconnect beim Verbindungsende
        with client.websocket_connect(f"/ws/lagedokument/{lage_id}"):
            pass


def test_ws_readonly_wird_abgelehnt(client, setup_db):
    _, lage_id = _make_user_with_lage("ldws_ro", org_slug="ldws-ro", rolle="readonly")
    _login(client, "ldws_ro", "Test1234!")
    with pytest.raises(Exception):  # noqa: B017
        with client.websocket_connect(f"/ws/lagedokument/{lage_id}"):
            pass


def test_ws_editor_kann_verbinden_und_sync_step1_empfangen(client, setup_db):
    _, lage_id = _make_user_with_lage("ldws_edit", org_slug="ldws-edit", rolle="incident_leader")
    _login(client, "ldws_edit", "Test1234!")
    with client.websocket_connect(f"/ws/lagedokument/{lage_id}") as ws:
        # Server schickt beim Verbinden sofort eine SYNC_STEP1-Nachricht (Y-Sync-Protokoll,
        # siehe pycrdt.websocket.yroom.YRoom.serve -- erstes Byte = messageSync = 0).
        msg = ws.receive_bytes()
        assert msg[0] == 0  # YMessageType.SYNC


# ── Regression: _FastAPIChannel muss von pycrdt.Channel erben ────────────────
# Bugfix 2026-07-15: YRoom.serve() iteriert per `async for message in channel:` --
# das setzt __aiter__/__anext__ voraus. _FastAPIChannel implementierte bisher nur
# path/send/recv OHNE von Channel zu erben und bekam die von Channel per Vererbung
# bereitgestellten __aiter__/__anext__ dadurch NICHT. Jede WS-Verbindung crashte direkt
# nach der ersten SYNC_STEP1-Nachricht mit "TypeError: 'async for' requires an object
# with __aiter__ method" -- die Live-Kollaboration hat dadurch nie echte Client-
# Aenderungen empfangen. Der obige Test (nur bis zum Empfang der ersten Nachricht) hat
# das nicht erkannt, da der Crash erst danach passiert (beim Betreten der async-for-Schleife).

class _FakeWebSocket:
    """Minimaler Stand-in fuer FastAPIs WebSocket, um YRoom.serve() direkt (ohne echten
    WS-Transport) gegen den echten _FastAPIChannel-Adapter zu pruefen."""
    def __init__(self, incoming: list[bytes]):
        self.sent: list[bytes] = []
        self._incoming = list(incoming)

    async def send_bytes(self, message: bytes) -> None:
        self.sent.append(message)

    async def receive_bytes(self) -> bytes:
        if self._incoming:
            return self._incoming.pop(0)
        raise RuntimeError("Client getrennt (simuliert)")


@pytest.mark.asyncio
async def test_fastapi_channel_ist_async_iterierbar():
    """Ohne den Fix wirft schon `channel.__aiter__()` einen AttributeError bzw.
    _FastAPIChannel() selbst ist kein gueltiges 'async for'-Ziel."""
    from app.routers.ui_lagedokument import _FastAPIChannel
    channel = _FastAPIChannel(_FakeWebSocket(incoming=[]), path="test")
    assert channel.__aiter__() is channel
    with pytest.raises(StopAsyncIteration):
        await channel.__anext__()  # kein Client-Byte vorhanden -> sauberes Streamende


@pytest.mark.asyncio
async def test_room_serve_mit_fastapi_channel_crasht_nicht_beim_ersten_iterationsschritt():
    """Direkter Repro-Test gegen die echte YRoom.serve()-Implementierung (nicht nur den
    Adapter isoliert): vor dem Fix brach dieser Aufruf mit der oben beschriebenen
    TypeError ab, nachdem genau eine SYNC_STEP1-Nachricht gesendet wurde."""
    from app.routers.ui_lagedokument import _FastAPIChannel
    from pycrdt.websocket.yroom import YRoom

    ws = _FakeWebSocket(incoming=[])  # "Client" trennt sofort nach dem Connect
    channel = _FastAPIChannel(ws, path="test-path")
    room = YRoom(Doc())
    await asyncio.wait_for(room.serve(channel), timeout=5)  # darf NICHT werfen
    assert len(ws.sent) == 1  # genau die initiale SYNC_STEP1-Nachricht
    assert ws.sent[0][0] == 0  # YMessageType.SYNC


@pytest.mark.asyncio
async def test_room_laedt_gespeicherten_zustand_und_speichert_bei_release(setup_db):
    org_id, lage_id = await asyncio.to_thread(
        _make_user_with_lage, "ldws_persist", org_slug="ldws-persist", rolle="incident_leader",
    )

    # Vorbereitung: ein Doc mit Inhalt erzeugen und dessen Zustand direkt in die DB schreiben,
    # um das Laden eines bestehenden Zustands beim Room-Aufbau zu pruefen.
    seed_doc = Doc()
    seed_text = seed_doc.get("content", type=Text)
    seed_text += "Vorbefuellter Text"
    seed_state = seed_doc.get_update()

    def _seed():
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            db.add(LageDokument(major_incident_id=lage_id, org_id=org_id, ydoc_state=seed_state,
                                updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC)))
            db.commit()
        finally:
            db.close()
    await asyncio.to_thread(_seed)

    room = await collab.get_or_create_room(lage_id, org_id)
    loaded_text = room.ydoc.get("content", type=Text)
    assert str(loaded_text) == "Vorbefuellter Text"

    # Aenderung vornehmen -> Room als "dirty" markiert (ueber den beim Erzeugen registrierten
    # observer), release_room_if_empty muss den letzten Stand speichern.
    loaded_text += "!"
    collab._dirty[lage_id] = True  # Debounce-Fenster (10s) hier nicht abwarten, direkt pruefen
    await collab.release_room_if_empty(lage_id)

    def _read_saved():
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            return db.query(LageDokument).filter(LageDokument.major_incident_id == lage_id).first().ydoc_state
        finally:
            db.close()
    saved_state = await asyncio.to_thread(_read_saved)

    check_doc = Doc()
    check_doc.apply_update(saved_state)
    assert str(check_doc.get("content", type=Text)) == "Vorbefuellter Text!"
    assert lage_id not in collab._rooms


def test_strip_html_to_text():
    html = "<p>Erster Absatz</p><p>Zweiter <strong>Absatz</strong> &amp; mehr</p>"
    assert collab._strip_html_to_text(html) == "Erster Absatz\nZweiter Absatz & mehr"


@pytest.mark.asyncio
async def test_room_bootstrapt_aus_content_html_wenn_kein_ydoc_state_vorhanden(setup_db):
    """Uebergangsfall: ein per klassischem Speichern-Formular (PR1) entstandenes
    Lagedokument hat noch keinen ydoc_state -- der erste Live-Kollaborations-
    Aufbau muss den vorhandenen content_html-Snapshot als Klartext uebernehmen,
    statt mit einem leeren Dokument zu starten."""
    org_id, lage_id = await asyncio.to_thread(
        _make_user_with_lage, "ldws_bootstrap", org_slug="ldws-bootstrap", rolle="incident_leader",
    )

    def _seed_html():
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            db.add(LageDokument(major_incident_id=lage_id, org_id=org_id,
                                content_html="<p>Klassisch gespeicherter Text</p>",
                                updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC)))
            db.commit()
        finally:
            db.close()
    await asyncio.to_thread(_seed_html)

    room = await collab.get_or_create_room(lage_id, org_id)
    assert str(room.ydoc.get("content", type=Text)) == "Klassisch gespeicherter Text"
    await collab.release_room_if_empty(lage_id)
