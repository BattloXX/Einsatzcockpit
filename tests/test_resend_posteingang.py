"""Sicherheitsregressionen für den Resend-Posteingang."""
from __future__ import annotations

from app.services.resend_inbound_service import (
    MAX_ATTACHMENT_BYTES,
    attachment_content_type,
    safe_attachment_filename,
    safe_attachment_url,
)


def test_attachment_hosts_and_filename_are_restricted():
    assert safe_attachment_url("https://files.resend.com/download/x")
    assert safe_attachment_url("https://bucket.s3.amazonaws.com/x", from_resend_api=True)
    assert not safe_attachment_url("https://bucket.s3.amazonaws.com/x")
    assert not safe_attachment_url("https://example.invalid/download")
    assert not safe_attachment_url("http://files.resend.com/download")
    assert safe_attachment_filename('../../evil\x00".pdf') == "_.._evil_.pdf"


def test_attachment_response_headers_are_safe_types_and_limited():
    assert MAX_ATTACHMENT_BYTES == 25 * 1024 * 1024
    assert attachment_content_type("image/png; charset=binary") == "image/png"
    assert attachment_content_type("text/html") == "application/octet-stream"


# ── Integrationstests: Webhook, Rechte, Mandanten, Ansichten ───────────────────────────────
import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.crypto import encrypt_secret
from app.core.security import hash_password, sign_mail_inbound_webhook_org
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.org_mail import OrgMailEingang, OrgResendConfig
from app.models.user import Role, User, UserRole
from app.services import resend_inbound_service as inbound

SECRET = b"inbound-test-secret"


def _signiert(body: bytes, ident="evt_1", timestamp=None):
    stamp = str(timestamp or int(time.time()))
    wert = base64.b64encode(hmac.new(SECRET, f"{ident}.{stamp}.{body.decode()}".encode(), hashlib.sha256).digest()).decode()
    return {"svix-id": ident, "svix-timestamp": stamp, "svix-signature": "v1," + wert,
            "content-type": "application/json"}


def _org(rolle="admin", *, aktiv=True):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        suffix = uuid4().hex[:8]
        org = FireDept(slug="posteingang-" + suffix, name="Posteingang", color="#f00", bos="Feuerwehr")
        db.add(org)
        db.flush()
        db.add(OrgResendConfig(
            org_id=org.id, enabled=True, api_key_enc=encrypt_secret("re_testkey"), from_addr="x@example.at",
            inbound_enabled=aktiv, inbound_webhook_secret_enc=encrypt_secret("whsec_" + base64.b64encode(SECRET).decode()),
        ))
        user = User(username="pe-" + suffix, password_hash=hash_password("Test1234!"), display_name="T",
                    org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == rolle).one().id))
        db.commit()
        return org.id, user.username
    finally:
        db.close()


def _login(client, username):
    client.get("/login")
    antwort = client.post("/login", data={"username": username, "password": "Test1234!",
                                          "_csrf": client.cookies.get("ec_csrf")})
    assert antwort.status_code == 200


def _event(email_id="mail_1", betreff="Lagebild"):
    return json.dumps({"type": "email.received", "data": {
        "email_id": email_id, "from": "Leitstelle <lls@example.at>", "to": ["feuerwehr.x@example.at"],
        "subject": betreff, "message_id": "<a@b>", "created_at": datetime.now(UTC).isoformat(),
        "attachments": [{"id": "att_1", "filename": "plan.pdf", "content_type": "application/pdf"}],
    }}).encode()


@pytest.fixture
def kein_abruf(monkeypatch):
    aufrufe = []

    async def abruf(org_id, message_id):
        aufrufe.append((org_id, message_id))

    monkeypatch.setattr("app.routers.mail_inbound_webhook.fetch_inbound_background", abruf)
    return aufrufe


def _eintraege(org_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(OrgMailEingang).filter_by(org_id=org_id).all()
    finally:
        db.close()


def test_webhook_gueltig_legt_eine_zeile_an_und_ist_idempotent(client, setup_db, kein_abruf):
    org_id, _ = _org()
    pfad = f"/mail/webhook/resend-inbound/{sign_mail_inbound_webhook_org(org_id)}"
    body = _event()
    assert client.post(pfad, content=body, headers=_signiert(body)).status_code == 200
    assert client.post(pfad, content=body, headers=_signiert(body, ident="evt_2")).status_code == 200
    zeilen = _eintraege(org_id)
    assert len(zeilen) == 1 and zeilen[0].betreff == "Lagebild" and zeilen[0].status == "neu"
    assert len(kein_abruf) == 1


@pytest.mark.parametrize("fall", ["falsche_signatur", "zu_alt", "header_fehlen", "fremdes_event", "inbound_aus"])
def test_webhook_lehnt_ab_oder_ignoriert(client, setup_db, kein_abruf, fall):
    org_id, _ = _org(aktiv=fall != "inbound_aus")
    pfad = f"/mail/webhook/resend-inbound/{sign_mail_inbound_webhook_org(org_id)}"
    body = _event()
    headers = _signiert(body)
    erwartet = 200
    if fall == "falsche_signatur":
        headers["svix-signature"] = "v1,AAAA"
        erwartet = 401
    elif fall == "zu_alt":
        headers = _signiert(body, timestamp=int(time.time()) - 3600)
        erwartet = 401
    elif fall == "header_fehlen":
        headers.pop("svix-id")
        erwartet = 400
    elif fall == "fremdes_event":
        body = json.dumps({"type": "email.delivered", "data": {}}).encode()
        headers = _signiert(body)
    elif fall == "inbound_aus":
        erwartet = 404
    assert client.post(pfad, content=body, headers=headers).status_code == erwartet
    assert _eintraege(org_id) == [] and kein_abruf == []


def test_webhook_ungueltiges_token_und_zu_grosse_nachricht(client, setup_db, kein_abruf):
    assert client.post("/mail/webhook/resend-inbound/unsinn", content=b"{}").status_code == 404
    org_id, _ = _org()
    pfad = f"/mail/webhook/resend-inbound/{sign_mail_inbound_webhook_org(org_id)}"
    gross = b"x" * (300 * 1024)
    assert client.post(pfad, content=gross, headers=_signiert(gross)).status_code == 413


def test_mandantentrennung_ansichten_und_rechte(client, setup_db, kein_abruf):
    org_a, admin_a = _org()
    org_b, admin_b = _org()
    body = _event(email_id="mail_a")
    client.post(f"/mail/webhook/resend-inbound/{sign_mail_inbound_webhook_org(org_a)}", content=body,
                headers=_signiert(body))
    eintrag_id = _eintraege(org_a)[0].id
    assert _eintraege(org_b) == []
    _login(client, admin_b)
    for pfad in (f"/admin/mail/eingang/{eintrag_id}", f"/admin/mail/eingang/{eintrag_id}/html",
                 f"/admin/mail/eingang/{eintrag_id}/anhang/att_1"):
        assert client.get(pfad).status_code == 404, pfad
    assert "mail_a" not in client.get("/admin/mail/eingang").text
    csrf = client.cookies.get("ec_csrf")
    assert client.post(f"/admin/mail/eingang/{eintrag_id}/loeschen", data={"_csrf": csrf}).status_code == 404
    assert len(_eintraege(org_a)) == 1
    client.cookies.clear()
    _login(client, admin_a)
    assert client.get(f"/admin/mail/eingang/{eintrag_id}").status_code == 200
    antwort = client.get(f"/admin/mail/eingang/{eintrag_id}/html")
    assert antwort.status_code == 200 and "default-src 'none'" in antwort.headers["content-security-policy"]
    assert antwort.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("rolle", ["recorder", "readonly", "incident_leader"])
def test_posteingang_nur_fuer_org_admin(client, setup_db, rolle):
    _, username = _org(rolle)
    _login(client, username)
    assert client.get("/admin/mail/eingang").status_code in (401, 403)


def test_secret_und_api_key_erscheinen_nie_in_der_seite(client, setup_db):
    _, admin = _org()
    _login(client, admin)
    for pfad in ("/admin/mail", "/admin/mail/eingang"):
        text = client.get(pfad).text
        assert "re_testkey" not in text and base64.b64encode(SECRET).decode() not in text


def test_detail_zeigt_html_nicht_inline_und_loeschen_funktioniert(client, setup_db, kein_abruf):
    org_id, admin = _org()
    body = _event()
    client.post(f"/mail/webhook/resend-inbound/{sign_mail_inbound_webhook_org(org_id)}", content=body,
                headers=_signiert(body))
    db = SessionLocal()
    set_tenant_context(db, None)
    zeile = db.query(OrgMailEingang).filter_by(org_id=org_id).one()
    zeile.html_body = "<script>alert('x')</script><p>Hallo</p>"
    zeile.text_body = "Hallo Text"
    db.commit()
    eintrag_id = zeile.id
    db.close()
    _login(client, admin)
    seite = client.get(f"/admin/mail/eingang/{eintrag_id}").text
    assert "<script>alert" not in seite and "Hallo Text" in seite and "sandbox" in seite
    csrf = client.cookies.get("ec_csrf")
    client.post(f"/admin/mail/eingang/{eintrag_id}/loeschen", data={"_csrf": csrf})
    assert _eintraege(org_id) == []


def test_retention_loescht_nur_alte_eintraege_der_eigenen_org(setup_db):
    org_a, _ = _org()
    org_b, _ = _org()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        alt = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=200)
        neu = datetime.now(UTC).replace(tzinfo=None)
        for org, zeit, mid in ((org_a, alt, "alt_a"), (org_a, neu, "neu_a"), (org_b, alt, "alt_b")):
            db.add(OrgMailEingang(org_id=org, resend_email_id=mid + uuid4().hex[:4], absender="x", empfaenger="[]",
                                  betreff="b", empfangen_at=zeit, status="neu"))
        db.query(OrgResendConfig).filter_by(org_id=org_a).one().inbound_retention_days = 90
        db.query(OrgResendConfig).filter_by(org_id=org_b).one().inbound_retention_days = 365
        db.commit()
        inbound.loesche_alte_eingaenge(db)
    finally:
        db.close()
    assert [z.resend_email_id[:5] for z in _eintraege(org_a)] == ["neu_a"]
    assert len(_eintraege(org_b)) == 1


def test_steuerzeichen_und_laenge_werden_begrenzt():
    assert inbound.clean_text("A\x00B\r\nC" + "x" * 2000, 20) == "AB  Cxxxxxxxxxxxxxxx"[:20]


def test_abruf_fehler_wird_generisch_gespeichert_und_abruf_erfolg_setzt_inhalt(setup_db, monkeypatch):
    import asyncio

    org_id, _ = _org()
    db = SessionLocal()
    set_tenant_context(db, None)
    zeile = OrgMailEingang(org_id=org_id, resend_email_id="mail_x", absender="a", empfaenger="[]", betreff="b",
                           empfangen_at=datetime.now(UTC).replace(tzinfo=None), status="neu")
    db.add(zeile)
    db.commit()
    eintrag_id = zeile.id

    class Antwort:
        def __init__(self, ok, daten=None):
            self.is_success, self._daten = ok, daten or {}

        def json(self):
            return self._daten

    class Client:
        def __init__(self, antwort):
            self.antwort = antwort

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            assert url.startswith("https://api.resend.com/emails/receiving/")
            return self.antwort

    monkeypatch.setattr(inbound.httpx, "AsyncClient", lambda **kw: Client(Antwort(False)))
    asyncio.run(inbound.fetch_inbound_message(db, org_id, eintrag_id))
    db.refresh(zeile)
    assert zeile.status == "abruf_fehler" and "re_testkey" not in (zeile.fehler or "")
    monkeypatch.setattr(inbound.httpx, "AsyncClient", lambda **kw: Client(Antwort(True, {
        "subject": "Neu", "from": "a@b.at", "to": ["x@y.at"], "text": "Hallo", "html": "<p>Hallo</p>",
        "spf": "pass", "attachments": [{"id": "att_9", "filename": "a.pdf", "size": 5}],
    })))
    asyncio.run(inbound.fetch_inbound_message(db, org_id, eintrag_id))
    db.refresh(zeile)
    assert zeile.status == "neu" and zeile.text_body == "Hallo" and zeile.spf == "pass" and zeile.fehler is None
    assert "att_9" in zeile.anhang_json and "download_url" not in zeile.anhang_json
    db.close()
