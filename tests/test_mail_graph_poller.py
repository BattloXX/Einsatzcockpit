"""Gezielte Tests für den ausschließlich lesenden Microsoft-Graph-Abruf."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.config import settings
from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.org_mail import OrgMailEingang, OrgO365MailConfig
from app.services import mail_graph_poller as poller
from tests.test_resend_posteingang import _org


@pytest.fixture(autouse=True)
def _graph_abruf_aktiv(monkeypatch):
    """conftest schaltet den Poll-Loop global ab; die Abruf-Funktion selbst wird hier direkt getestet."""
    monkeypatch.setattr(settings, "MAIL_GRAPH_POLL_ENABLED", True)


def _raw(message_id: str, attachment: bool = False) -> bytes:
    extra = (
        "\r\n--x\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=a.pdf\r\n"
        "Content-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n--x--"
        if attachment else ""
    )
    content_type = "multipart/mixed; boundary=x" if attachment else "text/plain"
    return (
        f"From: Leitstelle <lls@example.at>\r\nSubject: Graph {message_id}\r\n"
        f"Message-ID: <{message_id}@example.at>\r\nDate: Sat, 11 Oct 2026 12:00:00 +0000\r\n"
        f"Content-Type: {content_type}\r\n\r\nHallo{extra}"
    ).encode()


def _item(message_id: str, when: str = "2026-10-11T12:00:00Z") -> dict:
    return {
        "id": message_id, "internetMessageId": f"<{message_id}@example.at>",
        "receivedDateTime": when, "subject": f"Graph {message_id}",
        "from": {"emailAddress": {"address": "lls@example.at"}},
    }


class _Response:
    def __init__(self, status_code: int, data=None, content: bytes = b""):
        self.status_code, self._data, self.content = status_code, data or {}, content

    def json(self):
        return self._data


class _Stream:
    def __init__(self, response: _Response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return None


class _GraphClient:
    pages: list[_Response] = []
    mime: dict[str, _Response] = {}
    calls: list[tuple] = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.pages.pop(0)

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _Stream(self.mime.get(url.rsplit("/", 2)[-2], _Response(404)))


async def _aiter_bytes(self):
    yield self.content


_Response.aiter_bytes = _aiter_bytes


@pytest.fixture
def graph(monkeypatch):
    _GraphClient.pages, _GraphClient.mime, _GraphClient.calls = [], {}, []
    monkeypatch.setattr(poller.httpx, "AsyncClient", _GraphClient)
    monkeypatch.setattr(poller, "_acquire_token", lambda **_kwargs: _token())
    return _GraphClient


async def _token():
    return "token"


def _config(org_id: int, **values):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(OrgSettings(org_id=org_id, mail_eingang_modus="freigegeben", mail_eingang_max_pro_stunde=30))
        cfg = OrgO365MailConfig(
            org_id=org_id, read_enabled=True, tenant_id="tenant", client_id="client",
            client_secret_enc=encrypt_secret("secret"), sender_address="sender@example.at",
            read_aktiv_seit=datetime(2026, 10, 11, 12),
        )
        for name, value in values.items():
            setattr(cfg, name, value)
        db.add(cfg)
        db.commit()
    finally:
        db.close()


def _rows(org_id: int):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgO365MailConfig).filter_by(org_id=org_id).one()
        rows = db.query(OrgMailEingang).filter_by(org_id=org_id, quelle="o365").all()
        return rows, cfg
    finally:
        db.close()


@pytest.mark.asyncio
async def test_erstlauf_24h_und_folgelauf(setup_db, graph, monkeypatch):
    org_id, _ = _org()
    _config(org_id)
    monkeypatch.setattr(poller, "_now", lambda: datetime(2026, 10, 11, 12, 30))
    graph.pages = [_Response(200, {"value": [_item("one"), _item("two", "2026-10-11T12:01:00Z")]})]
    graph.mime = {"one": _Response(200, content=_raw("one")), "two": _Response(200, content=_raw("two"))}
    assert await poller.abrufen_fuer_org(org_id) == 2
    assert "2026-10-10T12:00:00Z" in graph.calls[0][2]["params"]["$filter"]
    rows, cfg = _rows(org_id)
    assert len(rows) == 2 and cfg.read_seit == datetime(2026, 10, 11, 12, 1)
    graph.pages = [_Response(200, {"value": [_item("three", "2026-10-11T12:02:00Z")]})]
    graph.mime = {"three": _Response(200, content=_raw("three"))}
    assert await poller.abrufen_fuer_org(org_id) == 1


@pytest.mark.asyncio
async def test_paginierung_ueber_nextlink_und_fremde_nextlink_domain_wird_ignoriert(setup_db, graph):
    org_id, _ = _org()
    _config(org_id)
    next_url = f"{poller.GRAPH_BASE_URL}/next"
    graph.pages = [
        _Response(200, {"value": [_item("one")], "@odata.nextLink": next_url}),
        _Response(200, {"value": [_item("two")], "@odata.nextLink": "https://evil.example/next"}),
    ]
    graph.mime = {"one": _Response(200, content=_raw("one")), "two": _Response(200, content=_raw("two"))}
    assert await poller.abrufen_fuer_org(org_id) == 2
    assert [call[1] for call in graph.calls if call[0] == "GET"].count(next_url) == 1


@pytest.mark.asyncio
async def test_wiederholung_ohne_duplikate(setup_db, graph):
    org_id, _ = _org()
    _config(org_id)
    for _ in range(2):
        graph.pages = [_Response(200, {"value": [_item("one")]})]
        graph.mime = {"one": _Response(200, content=_raw("one"))}
        await poller.abrufen_fuer_org(org_id)
    assert len(_rows(org_id)[0]) == 1


@pytest.mark.asyncio
async def test_401_erneuert_token_einmal(setup_db, graph, monkeypatch):
    org_id, _ = _org()
    _config(org_id)
    calls = []

    async def token(**kwargs):
        calls.append(kwargs)
        return "fresh"

    monkeypatch.setattr(poller, "_acquire_token", token)
    graph.pages = [_Response(401), _Response(200, {"value": []})]
    assert await poller.abrufen_fuer_org(org_id) == 0
    assert [call.get("force") for call in calls] == [None, True]


@pytest.mark.asyncio
async def test_403_setzt_fehlertext(setup_db, graph):
    org_id, _ = _org()
    _config(org_id)
    graph.pages = [_Response(403), _Response(403)]
    await poller.abrufen_fuer_org(org_id)
    assert "Mail.Read" in (_rows(org_id)[1].read_letzter_fehler or "")


@pytest.mark.asyncio
async def test_mime_anhang_wird_lokal_gespeichert(setup_db, graph, monkeypatch, tmp_path):
    org_id, _ = _org()
    _config(org_id)
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    graph.pages = [_Response(200, {"value": [_item("one")]})]
    graph.mime = {"one": _Response(200, content=_raw("one", attachment=True))}
    assert await poller.abrufen_fuer_org(org_id) == 1
    assert _rows(org_id)[0][0].anhang_speicher == "lokal"


@pytest.mark.asyncio
async def test_zu_grosse_mail_nur_metadaten(setup_db, graph, monkeypatch):
    from app.services import mail_eingang_anhang

    org_id, _ = _org()
    _config(org_id)
    monkeypatch.setattr(mail_eingang_anhang, "MAX_MAIL_BYTES", 3)
    graph.pages = [_Response(200, {"value": [_item("one")]})]
    graph.mime = {"one": _Response(200, content=_raw("one"))}
    assert await poller.abrufen_fuer_org(org_id) == 1
    row = _rows(org_id)[0][0]
    assert row.status == "posteingang" and not row.text_body


@pytest.mark.asyncio
async def test_stundenlimit_haelt_marke_an(setup_db, graph):
    org_id, _ = _org()
    _config(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    db.query(OrgSettings).filter_by(org_id=org_id).update({"mail_eingang_max_pro_stunde": 1})
    db.commit()
    db.close()
    graph.pages = [_Response(200, {"value": [_item("one"), _item("two", "2026-10-11T12:01:00Z")]})]
    graph.mime = {"one": _Response(200, content=_raw("one")), "two": _Response(200, content=_raw("two"))}
    assert await poller.abrufen_fuer_org(org_id) == 1
    assert _rows(org_id)[1].read_seit == datetime(2026, 10, 11, 12)


@pytest.mark.asyncio
async def test_automatische_zuordnung(setup_db, graph, monkeypatch):
    from app.models.incident import Message
    from app.services import mail_zuordnung_service
    from tests.test_mail_zuordnung_integration import _einsatz

    async def no_broadcast(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mail_zuordnung_service, "broadcast_message_created", no_broadcast)
    org_id, _ = _org()
    _config(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    db.query(OrgSettings).filter_by(org_id=org_id).update({"mail_eingang_absender": "@example.at"})
    db.commit()
    db.close()
    incident_id = _einsatz(org_id)
    graph.pages = [_Response(200, {"value": [_item("one")]})]
    graph.mime = {"one": _Response(200, content=_raw("one"))}
    await poller.abrufen_fuer_org(org_id)
    row = _rows(org_id)[0][0]
    assert row.status == "zugeordnet" and row.incident_id == incident_id and row.message_id_ref
    assert SessionLocal().get(Message, row.message_id_ref) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [{"read_enabled": False}, {"mail_eingang_modus": "aus"}])
async def test_deaktivierte_org_wird_nicht_abgerufen(setup_db, graph, values):
    org_id, _ = _org()
    _config(org_id, **values)
    if "mail_eingang_modus" in values:
        db = SessionLocal()
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=org_id).update(values)
        db.commit()
        db.close()
    assert await poller.abrufen_fuer_org(org_id) == 0
    assert graph.calls == []


@pytest.mark.asyncio
async def test_postfach_fallback_auf_sender_address(setup_db, graph):
    org_id, _ = _org()
    _config(org_id, read_postfach=None)
    graph.pages = [_Response(200, {"value": []})]
    await poller.abrufen_fuer_org(org_id)
    assert "sender%40example.at" in graph.calls[0][1]


def test_einschalten_setzt_startzeit_zurueck_und_ungueltiges_read_postfach_flash(setup_db, client):
    from tests.test_resend_posteingang import _login

    org_id, username = _org()
    _config(org_id, read_enabled=False, read_seit=datetime(2026, 1, 1), read_letzter_fehler="alt")
    _login(client, username)
    data = {"_csrf": client.cookies.get("ec_csrf"), "read_enabled": "1", "read_postfach": "bad"}
    response = client.post("/admin/mail/o365/save", data=data, follow_redirects=False)
    assert "error_read_postfach" in response.headers["location"]
    data["read_postfach"] = "read@example.at"
    response = client.post("/admin/mail/o365/save", data=data, follow_redirects=False)
    assert response.status_code in (302, 303)
    cfg = _rows(org_id)[1]
    assert cfg.read_aktiv_seit is not None and cfg.read_seit is None and cfg.read_letzter_fehler is None


@pytest.mark.asyncio
async def test_verbindung_testen(graph):
    cfg = type("Cfg", (), {
        "org_id": 1, "tenant_id": "tenant", "client_id": "client", "client_secret_enc": encrypt_secret("secret"),
        "sender_address": "sender@example.at", "read_postfach": None,
    })()
    graph.pages = [_Response(200, {"totalItemCount": 7})]
    ok, text = await poller.verbindung_testen(cfg)
    assert ok and text == "Verbindung erfolgreich, 7 Nachrichten im Posteingang"


def test_main_registers_mail_graph_loop():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/main.py").read_text()
    assert "from app.services.mail_graph_poller import mail_graph_poll_loop" in source
    assert "mail_graph_poll_loop()," in source


@pytest.mark.asyncio
async def test_bekannte_mail_wird_nicht_erneut_heruntergeladen(setup_db, graph):
    org_id, _ = _org()
    _config(org_id)
    graph.pages = [_Response(200, {"value": [_item("one")]})]
    graph.mime = {"one": _Response(200, content=_raw("one"))}
    await poller.abrufen_fuer_org(org_id)
    graph.calls.clear()
    # "ge <Marke>" liefert die letzte Mail erneut – ihr MIME-Inhalt darf nicht nochmal geladen werden.
    graph.pages = [_Response(200, {"value": [_item("one")]})]
    assert await poller.abrufen_fuer_org(org_id) == 0
    assert not [call for call in graph.calls if call[1].endswith("/$value")]


@pytest.mark.asyncio
async def test_voruebergehender_fehler_beim_inhalt_haelt_marke_an(setup_db, graph):
    org_id, _ = _org()
    _config(org_id)
    graph.pages = [_Response(200, {"value": [_item("one")]})]
    graph.mime = {"one": _Response(503)}
    assert await poller.abrufen_fuer_org(org_id) == 0
    rows, cfg = _rows(org_id)
    assert rows == [] and cfg.read_seit is None and cfg.read_letzter_fehler
    graph.pages = [_Response(200, {"value": [_item("one")]})]
    graph.mime = {"one": _Response(200, content=_raw("one"))}
    assert await poller.abrufen_fuer_org(org_id) == 1
    rows, cfg = _rows(org_id)
    assert [row.status for row in rows] != ["abruf_fehler"] and cfg.read_letzter_fehler is None
