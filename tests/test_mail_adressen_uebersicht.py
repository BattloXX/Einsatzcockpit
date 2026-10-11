"""Regressionen fuer Adress-Uebersicht und Verbindungstests der Mail-Einstellungen."""
from __future__ import annotations

from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings
from app.models.org_mail import OrgResendConfig, OrgSmtpConfig
from app.models.user import AuditLog
from app.services import mail_service
from app.services.org_mail_adresse import mail_adressen_uebersicht
from tests.test_resend_posteingang import _login, _org


def test_versandweg_global_resend_matches_deliver(setup_db, monkeypatch):
    org_id, _ = _org()
    sent = []

    async def send_via_resend(*_args):
        sent.append("resend_global")

    monkeypatch.setattr(mail_service, "get_resend_cfg", lambda _db: {"api_key": "key", "from_domain": "example.at"})
    monkeypatch.setattr("app.services.resend_mail_service.send_via_resend", send_via_resend)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgResendConfig).filter_by(org_id=org_id).one().enabled = False
        db.commit()
        assert mail_service.versandweg(db, org_id)["weg"] == "resend_global"
        awaitable = mail_service.deliver(db, org_id, mail_service._build_message(
            to="to@example.at", subject="Test", body_txt="Test",
        ))
        import asyncio

        asyncio.run(awaitable)
    finally:
        db.close()
    assert sent == ["resend_global"]


def test_mail_adressen_uebersicht_and_mail_settings_card(setup_db, client, monkeypatch):
    org_id, username = _org()
    monkeypatch.setattr(mail_service, "get_resend_cfg", lambda _db: {"api_key": "key", "from_domain": "example.at"})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.get(FireDept, org_id)
        db.query(OrgResendConfig).filter_by(org_id=org_id).one().enabled = False
        db.add(OrgSettings(org_id=org_id, mail_eingang_modus="alle"))
        db.commit()
        overview = mail_adressen_uebersicht(db, org)
        assert overview["system"]["absender"].endswith("@example.at")
        assert overview["system"]["alias"] == f"{org.slug}@get.einsatzcockpit.com"
        assert overview["system"]["empfang_aktiv"]
        assert overview["versand"]["weg"] == "resend_global"
    finally:
        db.close()
    _login(client, username)
    page = client.get("/admin/mail").text
    assert "Ihre Mail-Adressen" in page and "wird verwendet" in page and "Empfangsadresse" in page


def test_imap_connection_test_redirects_and_writes_audit(setup_db, client, monkeypatch):
    org_id, username = _org()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(OrgSmtpConfig(
            org_id=org_id, imap_host="imap.example.at", user="mail@example.at",
            password_enc=encrypt_secret("secret"), imap_ordner="INBOX",
        ))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr("app.services.mail_imap_poller.verbindung_testen", lambda _cfg: (True, "IMAP bereit"))
    _login(client, username)
    response = client.post("/admin/mail/imap/verbindung-testen", data={"_csrf": client.cookies.get("ec_csrf")},
                           follow_redirects=False)
    assert response.status_code == 302 and "test_ok=1" in response.headers["location"]
    assert "IMAP bereit" in client.get(response.headers["location"]).text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(AuditLog).filter_by(action="org_mail.imap.test", org_id=org_id).count() == 1
    finally:
        db.close()


def test_smtp_save_keeps_imap_values(setup_db, client):
    org_id, username = _org()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(OrgSmtpConfig(org_id=org_id, imap_enabled=True, imap_host="imap.example.at", imap_port=993,
                             imap_use_ssl=True, imap_ordner="INBOX"))
        db.commit()
    finally:
        db.close()
    _login(client, username)
    response = client.post("/admin/mail/smtp/save", data={
        "_csrf": client.cookies.get("ec_csrf"), "host": "smtp.example.at", "smtp_user": "mail@example.at",
        "imap_enabled": "1", "imap_host": "imap.example.at", "imap_port": "993", "imap_use_ssl": "1",
        "imap_ordner": "INBOX",
    }, follow_redirects=False)
    assert response.status_code == 302
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(OrgSmtpConfig).filter_by(org_id=org_id).one().imap_ordner == "INBOX"
    finally:
        db.close()


# ── Ergänzungen: alle Versandwege, Fallbacks, O365-Test, fremde Org ─────────

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app.models.org_mail import OrgO365MailConfig  # noqa: E402


def _versand_konfig(org_id, weg, monkeypatch):
    """Richtet genau den Versandweg ein, den deliver() zuerst nehmen soll."""
    monkeypatch.setattr(mail_service.settings, "O365_MAIL_ENABLED", True)
    monkeypatch.setattr(mail_service, "get_resend_cfg", lambda _db: None)
    monkeypatch.setattr(mail_service, "get_smtp_cfg", lambda _db=None: {"host": None})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgResendConfig).filter_by(org_id=org_id).one().enabled = weg == "resend_org"
        if weg == "o365":
            db.add(OrgO365MailConfig(org_id=org_id, enabled=True, tenant_id="t", client_id="c",
                                     client_secret_enc=encrypt_secret("s"), sender_address="o365@example.at"))
        if weg == "smtp_org":
            db.add(OrgSmtpConfig(org_id=org_id, enabled=True, host="smtp.example.at", port=587,
                                 user="smtp@example.at", password_enc=encrypt_secret("pw"),
                                 from_addr="smtp@example.at"))
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize("weg", ["o365", "resend_org", "smtp_org", "keiner"])
def test_versandweg_entspricht_deliver(setup_db, monkeypatch, weg):
    org_id, _ = _org()
    _versand_konfig(org_id, weg, monkeypatch)
    genommen = []

    async def graph(*_a, **_k):
        genommen.append("o365")

    async def resend(*_a, **_k):
        genommen.append("resend_org")

    async def smtp(_msg, cfg):
        genommen.append("smtp_org" if cfg.get("host") else "keiner")

    monkeypatch.setattr("app.services.o365_mail_service.send_via_graph", graph)
    monkeypatch.setattr("app.services.resend_mail_service.send_via_resend", resend)
    monkeypatch.setattr(mail_service, "_send", smtp)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert mail_service.versandweg(db, org_id)["weg"] == weg
        asyncio.run(mail_service.deliver(db, org_id, mail_service._build_message(
            to="to@example.at", subject="Test", body_txt="Test",
        )))
    finally:
        db.close()
    assert genommen == [weg]


def test_versandweg_listet_fallbacks(setup_db, monkeypatch):
    org_id, _ = _org()
    _versand_konfig(org_id, "o365", monkeypatch)
    monkeypatch.setattr(mail_service, "get_resend_cfg", lambda _db: {"api_key": "key", "from_domain": "example.at"})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        weg = mail_service.versandweg(db, org_id)
    finally:
        db.close()
    assert weg["weg"] == "o365" and weg["absender"] == "o365@example.at"
    assert weg["fallbacks"] == ["resend_global"]


def test_o365_verbindungstest_und_fremde_org(setup_db, client, monkeypatch):
    org_id, username = _org()
    fremd_id, _ = _org()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(OrgO365MailConfig(org_id=org_id, enabled=True, tenant_id="t", client_id="c",
                                 client_secret_enc=encrypt_secret("s"), sender_address="o365@example.at"))
        db.commit()
    finally:
        db.close()

    async def ok(_cfg):
        return False, "Zugriff verweigert: Mail.Read fehlt"

    monkeypatch.setattr("app.services.mail_graph_poller.verbindung_testen", ok)
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    response = client.post("/admin/mail/o365/verbindung-testen", data={"_csrf": csrf}, follow_redirects=False)
    assert response.status_code == 302 and "test_ok=0" in response.headers["location"]
    assert "Mail.Read fehlt" in client.get(response.headers["location"]).text
    # Eine fremde target_org_id wird für Org-Admins ignoriert: getestet wird immer die eigene Organisation.
    client.post("/admin/mail/o365/verbindung-testen", data={"_csrf": csrf, "target_org_id": str(fremd_id)},
                follow_redirects=False)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(AuditLog).filter_by(action="org_mail.o365.test", org_id=fremd_id).count() == 0
        assert db.query(AuditLog).filter_by(action="org_mail.o365.test", org_id=org_id).count() == 2
    finally:
        db.close()


def test_smtp_hauptformular_sendet_imap_felder_mit(setup_db, client):
    org_id, username = _org()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(OrgSmtpConfig(org_id=org_id, imap_enabled=True, imap_host="imap.example.at", imap_ordner="Einsatz"))
        db.commit()
    finally:
        db.close()
    _login(client, username)
    page = client.get("/admin/mail").text
    # Das SMTP-Hauptformular postet auf dieselbe Route; ohne versteckte IMAP-Felder würde es den Abruf abschalten.
    formular = page[page.index('action="/admin/mail/smtp/save"'):]
    formular = formular[:formular.index("</form>")]
    assert 'name="imap_enabled"' in formular and 'name="imap_ordner"' in formular
