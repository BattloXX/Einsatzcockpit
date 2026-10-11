"""Systemweiter Resend-Posteingang: Adresse bestimmt den Mandanten."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.org_mail import OrgMailEingang

SECRET = b"system-inbound-secret"


def _headers(body: bytes, timestamp: int | None = None) -> dict[str, str]:
    stamp = str(timestamp or int(time.time()))
    signed = hmac.new(SECRET, f"evt.{stamp}.{body.decode()}".encode(), hashlib.sha256).digest()
    value = base64.b64encode(signed).decode()
    return {
        "svix-id": "evt", "svix-timestamp": stamp, "svix-signature": "v1," + value,
        "content-type": "application/json",
    }


def _org(bos: str = "Feuerwehr", *, mode: str = "alle", limit: int = 30) -> tuple[int, str]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug="system-" + uuid4().hex[:10], name="System", color="#f00", bos=bos)
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, mail_eingang_modus=mode, mail_eingang_max_pro_stunde=limit))
        db.commit()
        return org.id, org.slug
    finally:
        db.close()


def _secret() -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.get(SystemSettings, "resend_inbound_webhook_secret")
        if row:
            row.value = encrypt_secret("whsec_" + base64.b64encode(SECRET).decode())
        else:
            db.add(SystemSettings(
                key="resend_inbound_webhook_secret",
                value=encrypt_secret("whsec_" + base64.b64encode(SECRET).decode()),
            ))
        db.commit()
    finally:
        db.close()


def _body(email_id: str, *addresses: str, received_for=None) -> bytes:
    data = {
        "email_id": email_id, "from": "x@example.at", "to": list(addresses),
        "created_at": datetime.now(UTC).isoformat(),
    }
    if received_for is not None:
        data["received_for"] = received_for
    return json.dumps({"type": "email.received", "data": data}).encode()


def _rows(org_id: int) -> list[OrgMailEingang]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(OrgMailEingang).filter_by(org_id=org_id).all()
    finally:
        db.close()


def test_system_webhook_ordnet_typ_alias_und_received_for_zu(client, setup_db, monkeypatch):
    one, two = _org(), _org("Rotes Kreuz")
    _secret()
    monkeypatch.setattr("app.routers.mail_inbound_webhook.fetch_inbound_background", lambda *_: None)
    typed = f"feuerwehr.{one[1]}@get.einsatzcockpit.com"
    alias = f"{two[1]}@get.einsatzcockpit.com"
    body = _body("system-1", typed, received_for=[alias])
    assert client.post("/mail/webhook/resend-inbound", content=body, headers=_headers(body)).status_code == 200
    assert len(_rows(one[0])) == 1
    assert len(_rows(two[0])) == 1


def test_system_webhook_ignoriert_unbekannt_aus_und_falsche_signatur(client, setup_db, monkeypatch):
    disabled = _org(mode="aus")
    _secret()
    monkeypatch.setattr("app.routers.mail_inbound_webhook.fetch_inbound_background", lambda *_: None)
    body = _body("system-2", f"feuerwehr.{disabled[1]}@get.einsatzcockpit.com", "unknown@else.invalid")
    assert client.post("/mail/webhook/resend-inbound", content=body, headers=_headers(body)).status_code == 200
    assert _rows(disabled[0]) == []
    bad = _headers(body)
    bad["svix-signature"] = "v1,nope"
    assert client.post("/mail/webhook/resend-inbound", content=body, headers=bad).status_code == 401
    response = client.post("/mail/webhook/resend-inbound", content=body,
                           headers=_headers(body, int(time.time()) - 3600))
    assert response.status_code == 401


def test_system_webhook_secret_duplicate_and_per_org_limit(client, setup_db, monkeypatch):
    limited, other = _org(limit=2), _org(limit=2)
    target = f"feuerwehr.{limited[1]}@get.einsatzcockpit.com"
    other_target = f"feuerwehr.{other[1]}@get.einsatzcockpit.com"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.get(SystemSettings, "resend_inbound_webhook_secret")
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()
    body = _body("no-secret", target)
    assert client.post("/mail/webhook/resend-inbound", content=body, headers=_headers(body)).status_code == 404
    _secret()
    monkeypatch.setattr("app.routers.mail_inbound_webhook.fetch_inbound_background", lambda *_: None)
    for email_id in ("one", "two", "three"):
        payload = _body(email_id, target)
        response = client.post("/mail/webhook/resend-inbound", content=payload, headers=_headers(payload))
        assert response.status_code == 200
    duplicate = _body("one", target)
    response = client.post("/mail/webhook/resend-inbound", content=duplicate, headers=_headers(duplicate))
    assert response.status_code == 200
    payload = _body("other", other_target)
    assert client.post("/mail/webhook/resend-inbound", content=payload, headers=_headers(payload)).status_code == 200
    assert len(_rows(limited[0])) == 2
    assert len(_rows(other[0])) == 1


def test_falscher_typ_zur_slug_wird_nicht_zugeordnet(client, setup_db, monkeypatch):
    org = _org("Feuerwehr")
    _secret()
    monkeypatch.setattr("app.routers.mail_inbound_webhook.fetch_inbound_background", lambda *_: None)
    body = _body("falsch-typ", f"rotes-kreuz.{org[1]}@get.einsatzcockpit.com")
    assert client.post("/mail/webhook/resend-inbound", content=body, headers=_headers(body)).status_code == 200
    assert _rows(org[0]) == []


def test_abruf_nutzt_org_key_sonst_globalen_key(setup_db):
    from app.core.crypto import encrypt_secret as enc
    from app.models.org_mail import OrgResendConfig
    from app.services.resend_inbound_service import resend_api_key_fuer_org

    mit_key, ohne_key = _org()[0], _org()[0]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        for key, wert in (("resend_enabled", "true"), ("resend_from_domain", "example.at"),
                          ("resend_api_key", enc("re_global"))):
            row = db.get(SystemSettings, key)
            if row:
                row.value = wert
            else:
                db.add(SystemSettings(key=key, value=wert))
        db.add(OrgResendConfig(org_id=mit_key, enabled=True, api_key_enc=enc("re_org"), from_addr="a@example.at"))
        db.commit()
        assert resend_api_key_fuer_org(db, mit_key) == "re_org"
        assert resend_api_key_fuer_org(db, ohne_key) == "re_global"
    finally:
        db.close()


def test_secret_wird_nie_ausgegeben_und_nur_system_admin_darf_es_setzen(client, setup_db):
    from app.core.security import hash_password
    from app.models.user import Role, User, UserRole

    def anlegen(rolle):
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            name = "sys-" + uuid4().hex[:8]
            org_id = None if rolle == "system_admin" else _org()[0]
            user = User(username=name, password_hash=hash_password("Test1234!"), display_name=name,
                        org_id=org_id, active=True)
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == rolle).one().id))
            db.commit()
            return name
        finally:
            db.close()

    def login(client, name):
        client.get("/login")
        client.post("/login", data={"username": name, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")})

    geheim = "whsec_" + base64.b64encode(b"sehr-geheim-123").decode()
    db = SessionLocal()
    set_tenant_context(db, None)
    alt = db.get(SystemSettings, "resend_inbound_webhook_secret")
    if alt:
        db.delete(alt)
        db.commit()
    db.close()
    client.cookies.clear()
    login(client, anlegen("admin"))
    antwort = client.post("/admin/system-einstellungen", data={
        "_csrf": client.cookies.get("ec_csrf"), "k_resend_inbound_webhook_secret": geheim,
        "k_resend_inbound_webhook_secret_changed": "1",
    }, follow_redirects=False)
    assert antwort.status_code in (302, 303, 401, 403)
    db = SessionLocal()
    set_tenant_context(db, None)
    vorher = db.get(SystemSettings, "resend_inbound_webhook_secret")
    vorher_wert = vorher.value if vorher else None
    db.close()
    client.cookies.clear()
    login(client, anlegen("system_admin"))
    client.post("/admin/system-einstellungen", data={
        "_csrf": client.cookies.get("ec_csrf"), "k_resend_inbound_webhook_secret": geheim,
        "k_resend_inbound_webhook_secret_changed": "1",
    }, follow_redirects=False)
    seite = client.get("/admin/system-einstellungen").text
    assert geheim not in seite and "sehr-geheim-123" not in seite
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gespeichert = db.get(SystemSettings, "resend_inbound_webhook_secret")
        assert vorher_wert is None  # org-admin darf das Secret nicht setzen
        assert gespeichert is not None and geheim not in gespeichert.value
    finally:
        db.close()
