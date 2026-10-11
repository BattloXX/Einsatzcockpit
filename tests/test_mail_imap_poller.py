"""Gezielte Tests für den ausschließlich lesenden IMAP-Abruf."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.config import settings
from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.org_mail import OrgMailEingang, OrgSmtpConfig
from app.services import mail_imap_poller as poller
from tests.test_resend_posteingang import _org

_IMAP_ERROR = poller.imaplib.IMAP4.error

_RAW = (
    b"From: Leitstelle <lls@example.at>\r\nSubject: IMAP-Test\r\n"
    b"Date: Sat, 11 Oct 2026 12:00:00 +0000\r\nContent-Type: text/plain\r\n\r\nHallo"
)


class FakeImap:
    calls: list[tuple] = []
    fail_login = False
    error = _IMAP_ERROR

    def __init__(self, *args, **kwargs):
        self.untagged_responses = {"UIDVALIDITY": [b"42"]}

    def login(self, user, password):
        self.calls.append(("LOGIN", user, password))
        if self.fail_login:
            raise self.error("bad credentials")
        return "OK", []

    def select(self, folder, readonly=False):
        self.calls.append(("SELECT", folder, readonly))
        return "OK", [b"2"]

    def response(self, name):
        return name, [b"42"]

    def uid(self, command, *args):
        self.calls.append(("UID", command, *args))
        if command == "SEARCH":
            return "OK", [b"1 2"]
        uid = str(args[0])
        if "RFC822.SIZE" in args[1]:
            return "OK", [(f"{uid} (RFC822.SIZE 100 INTERNALDATE \"11-Oct-2026 12:00:00 +0000\")".encode(), b"")]
        return "OK", [(b"1 (BODY[] {1}", _RAW)]

    def logout(self):
        self.calls.append(("LOGOUT",))


@pytest.fixture
def imap(monkeypatch):
    FakeImap.calls = []
    FakeImap.fail_login = False
    monkeypatch.setattr(poller.imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(poller.imaplib, "IMAP4", FakeImap)
    return FakeImap


def _config(org_id: int):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(OrgSettings(org_id=org_id, mail_eingang_modus="freigegeben", mail_eingang_max_pro_stunde=30))
        db.add(OrgSmtpConfig(
            org_id=org_id, imap_enabled=True, imap_host="imap.example.at", imap_port=993,
            imap_use_ssl=True, imap_ordner="INBOX", user="mail@example.at", password_enc=encrypt_secret("secret"),
        ))
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_first_run_stores_mail_and_uses_only_readonly_commands(setup_db, imap, monkeypatch, tmp_path):
    org_id, _ = _org()
    _config(org_id)
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(poller, "_now", lambda: datetime(2026, 10, 11, 12, 30))

    assert await poller.abrufen_fuer_org(org_id) == 2
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(OrgMailEingang).filter_by(org_id=org_id, quelle="imap").count() == 2
        cfg = db.query(OrgSmtpConfig).filter_by(org_id=org_id).one()
        assert (cfg.imap_uidvalidity, cfg.imap_letzte_uid) == (42, 2)
    finally:
        db.close()
    assert ("SELECT", "INBOX", True) in imap.calls
    commands = " ".join(" ".join(map(str, call)) for call in imap.calls)
    assert all(word not in commands for word in ("STORE", "COPY", "MOVE", "EXPUNGE", "APPEND"))
    assert "BODY.PEEK" in commands


@pytest.mark.asyncio
async def test_login_failure_does_not_advance_marker(setup_db, imap):
    org_id, _ = _org()
    _config(org_id)
    imap.fail_login = True

    assert await poller.abrufen_fuer_org(org_id) == 0
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgSmtpConfig).filter_by(org_id=org_id).one()
        assert cfg.imap_letzter_fehler == "Anmeldung fehlgeschlagen"
        assert cfg.imap_letzte_uid is None
        assert "secret" not in cfg.imap_letzter_fehler
    finally:
        db.close()


def test_main_registers_mail_imap_loop():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/main.py").read_text()
    assert "from app.services.mail_imap_poller import mail_imap_poll_loop" in source
    assert "mail_imap_poll_loop()," in source


# ── Datengetriebener Fake für Folgeläufe, Limits und Zuordnung ───────────────

def _mail_raw(uid: int, absender: str = "lls@example.at", groesse: int = 0) -> bytes:
    text = "Hallo" + ("x" * groesse)
    return (
        f"From: Leitstelle <{absender}>\r\nSubject: Mail {uid}\r\nMessage-ID: <m{uid}@example.at>\r\n"
        f"Date: Sat, 11 Oct 2026 12:00:00 +0000\r\nContent-Type: text/plain\r\n\r\n{text}"
    ).encode()


class PostfachImap(FakeImap):
    """Postfach mit {uid: (internaldate, raw)}; SEARCH UID n:* verhält sich wie echtes IMAP."""

    postfach: dict[int, tuple[str, bytes]] = {}
    uidvalidity = 42

    def __init__(self, *args, **kwargs):
        self.untagged_responses = {"UIDVALIDITY": [str(self.uidvalidity).encode()]}

    def response(self, name):
        return name, [str(self.uidvalidity).encode()]

    def uid(self, command, *args):
        self.calls.append(("UID", command, *args))
        if command == "SEARCH":
            if args[1] == "UID":
                start = int(args[2].split(":")[0])
                treffer = [uid for uid in self.postfach if uid >= start] or [max(self.postfach)]
            else:
                treffer = list(self.postfach)
            return "OK", [" ".join(map(str, sorted(treffer))).encode()]
        uid = int(args[0])
        datum, raw = self.postfach[uid]
        if "RFC822.SIZE" in args[1]:
            return "OK", [(f'{uid} (RFC822.SIZE {len(raw)} INTERNALDATE "{datum}")'.encode(), b"")]
        if "HEADER" in args[1]:
            return "OK", [(b"x", raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n")]
        return "OK", [(b"x", raw)]


@pytest.fixture
def postfach(monkeypatch, tmp_path):
    PostfachImap.calls = []
    PostfachImap.fail_login = False
    PostfachImap.uidvalidity = 42
    PostfachImap.postfach = {
        5: ("09-Oct-2026 08:00:00 +0000", _mail_raw(5)),  # älter als 24 h
        6: ("11-Oct-2026 11:00:00 +0000", _mail_raw(6)),
        7: ("11-Oct-2026 12:00:00 +0000", _mail_raw(7)),
    }
    monkeypatch.setattr(poller.imaplib, "IMAP4_SSL", PostfachImap)
    monkeypatch.setattr(poller.imaplib, "IMAP4", PostfachImap)
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(poller, "_now", lambda: datetime(2026, 10, 11, 12, 30))
    return PostfachImap


def _imap_eintraege(org_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(OrgMailEingang).filter_by(org_id=org_id, quelle="imap").order_by(OrgMailEingang.id).all()
        cfg = db.query(OrgSmtpConfig).filter_by(org_id=org_id).one()
        return [r.extern_id for r in rows], (cfg.imap_uidvalidity, cfg.imap_letzte_uid)
    finally:
        db.close()


def _setze(org_id, **werte):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        for modell in (OrgSettings, OrgSmtpConfig):
            row = db.query(modell).filter_by(org_id=org_id).one()
            for name, wert in werte.items():
                if hasattr(row, name):
                    setattr(row, name, wert)
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_erstlauf_24h_fenster_und_folgelauf_nur_neue_uids(setup_db, postfach):
    org_id, _ = _org()
    _config(org_id)
    assert await poller.abrufen_fuer_org(org_id) == 2
    assert _imap_eintraege(org_id) == (["42:6", "42:7"], (42, 7))
    # Keine neue Mail: IMAP liefert bei "8:*" die höchste vorhandene UID, die ignoriert wird.
    assert await poller.abrufen_fuer_org(org_id) == 0
    postfach.postfach[8] = ("11-Oct-2026 12:20:00 +0000", _mail_raw(8))
    assert await poller.abrufen_fuer_org(org_id) == 1
    assert _imap_eintraege(org_id) == (["42:6", "42:7", "42:8"], (42, 8))


@pytest.mark.asyncio
async def test_wiederholung_ohne_duplikate_und_uidvalidity_wechsel(setup_db, postfach):
    org_id, _ = _org()
    _config(org_id)
    await poller.abrufen_fuer_org(org_id)
    _setze(org_id, imap_letzte_uid=None)  # erzwingt erneuten Erstlauf mit denselben UIDs
    assert await poller.abrufen_fuer_org(org_id) == 0
    assert _imap_eintraege(org_id)[0] == ["42:6", "42:7"]
    postfach.uidvalidity = 43
    assert await poller.abrufen_fuer_org(org_id) == 2
    assert _imap_eintraege(org_id) == (["42:6", "42:7", "43:6", "43:7"], (43, 7))


@pytest.mark.asyncio
async def test_stundenlimit_haelt_marke_an(setup_db, postfach):
    org_id, _ = _org()
    _config(org_id)
    _setze(org_id, mail_eingang_max_pro_stunde=1)
    assert await poller.abrufen_fuer_org(org_id) == 1
    assert _imap_eintraege(org_id) == (["42:6"], (42, 6))
    _setze(org_id, mail_eingang_max_pro_stunde=30)
    assert await poller.abrufen_fuer_org(org_id) == 1
    assert _imap_eintraege(org_id) == (["42:6", "42:7"], (42, 7))


@pytest.mark.asyncio
async def test_zu_grosse_mail_nur_kopf(setup_db, postfach, monkeypatch):
    from app.models.org_mail import OrgMailEingangEreignis
    from app.services import mail_eingang_anhang

    org_id, _ = _org()
    _config(org_id)
    postfach.postfach = {9: ("11-Oct-2026 12:00:00 +0000", _mail_raw(9, groesse=5000))}
    monkeypatch.setattr(mail_eingang_anhang, "MAX_MAIL_BYTES", 1000)
    assert await poller.abrufen_fuer_org(org_id) == 1
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(OrgMailEingang).filter_by(org_id=org_id, quelle="imap").one()
        assert row.status == "posteingang" and row.betreff == "Mail 9" and not row.text_body
        typen = [e.typ for e in db.query(OrgMailEingangEreignis).filter_by(eingang_id=row.id)]
        assert "zu_gross" in typen
    finally:
        db.close()
    assert any(call[1] == "FETCH" and "HEADER" in call[3] for call in postfach.calls if call[0] == "UID")


@pytest.mark.asyncio
async def test_freigegebene_mail_wird_automatisch_zugeordnet(setup_db, postfach, monkeypatch):
    from app.models.incident import Message
    from app.services import mail_zuordnung_service
    from tests.test_mail_zuordnung_integration import _einsatz

    async def _still(*_a, **_k):
        return None

    monkeypatch.setattr(mail_zuordnung_service, "broadcast_message_created", _still)
    org_id, _ = _org()
    _config(org_id)
    _setze(org_id, mail_eingang_absender="@example.at")
    incident_id = _einsatz(org_id)
    postfach.postfach = {6: postfach.postfach[6]}
    assert await poller.abrufen_fuer_org(org_id) == 1
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(OrgMailEingang).filter_by(org_id=org_id, quelle="imap").one()
        assert (row.status, row.incident_id) == ("zugeordnet", incident_id)
        assert db.get(Message, row.message_id_ref).title == "Mail 6"
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("werte", [{"mail_eingang_modus": "aus"}, {"imap_enabled": False}])
async def test_deaktivierte_org_wird_nicht_abgerufen(setup_db, postfach, werte):
    org_id, _ = _org()
    _config(org_id)
    _setze(org_id, **werte)
    assert await poller.abrufen_fuer_org(org_id) == 0
    assert org_id not in poller._eligible_org_ids()
    assert postfach.calls == []


def test_einschalten_setzt_startzeit_und_marke_zurueck(setup_db, client):
    from tests.test_resend_posteingang import _login

    org_id, username = _org()
    _config(org_id)
    _setze(org_id, imap_enabled=False, imap_letzte_uid=99, imap_uidvalidity=1, imap_letzter_fehler="alt")
    _login(client, username)
    antwort = client.post("/admin/mail/smtp/save", data={
        "_csrf": client.cookies.get("ec_csrf"), "imap_enabled": "1", "imap_host": "imap.example.at",
        "imap_port": "993", "imap_use_ssl": "1", "imap_ordner": "Einsatz\x07", "smtp_user": "mail@example.at",
    }, follow_redirects=False)
    assert antwort.status_code in (302, 303)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgSmtpConfig).filter_by(org_id=org_id).one()
        assert cfg.imap_enabled and cfg.imap_aktiv_seit is not None
        assert (cfg.imap_letzte_uid, cfg.imap_uidvalidity, cfg.imap_letzter_fehler) == (None, None, None)
        assert cfg.imap_ordner == "Einsatz"
    finally:
        db.close()


def test_verbindung_testen_meldet_anzahl(postfach):
    ok, text = poller.verbindung_testen({
        "imap_host": "imap.example.at", "imap_port": 993, "imap_use_ssl": True,
        "user": "u", "password": "geheim", "imap_ordner": "INBOX",
    })
    assert ok and text == "Verbindung erfolgreich, 2 Nachrichten"
    postfach.fail_login = True
    ok, text = poller.verbindung_testen({
        "imap_host": "imap.example.at", "imap_port": 993, "imap_use_ssl": True,
        "user": "u", "password": "geheim",
    })
    assert not ok and text == "Anmeldung fehlgeschlagen" and "geheim" not in text
