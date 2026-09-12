"""Kommunikationsaktionen zentraler Kontakte (Phase 5)."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.kontakt import Kontakt, KontaktTelefon
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.sms import SmsLog
from app.models.user import Role, User, UserRole


def _setup_admin() -> tuple[str, int, int]:
    marker = uuid.uuid4().hex
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"p5-{marker}", name="Phase 5", color="#123456", bos="FW")
        db.add(org)
        db.flush()
        user = User(
            username=f"p5_{marker}", password_hash=hash_password("Test1234!"),
            display_name="Phase 5 Admin", org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code="admin").one().id))
        settings = OrgSettings(org_id=org.id, kontakte_module_enabled=True)
        db.add(settings)
        system = db.get(SystemSettings, "kontakte_module_enabled")
        if system is None:
            db.add(SystemSettings(key="kontakte_module_enabled", value="true"))
        else:
            system.value = "true"
        kontakt = Kontakt(org_id=org.id, typ="person", anzeigename="SMS Kontakt")
        db.add(kontakt)
        db.flush()
        telefon = KontaktTelefon(
            org_id=org.id, kontakt_id=kontakt.id, nummer="+43 664 55555", label="Mobil", sms_eignung=True,
        )
        db.add(telefon)
        db.commit()
        return user.username, kontakt.id, telefon.id
    finally:
        db.close()


def _login(client, username: str) -> str:
    client.get("/login")
    response = client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    return str(client.cookies.get("ec_csrf"))


def test_kontaktdetail_bietet_tel_und_sms_aktion(client):
    username, kontakt_id, _telefon_id = _setup_admin()
    _login(client, username)
    response = client.get(f"/kontakte/{kontakt_id}")
    assert response.status_code == 200
    assert "tel:+4366455555" in response.text
    assert "sms:+4366455555" in response.text


def test_gateway_sms_ist_auf_einen_kontakt_und_telefon_begrenzt(client):
    username, kontakt_id, telefon_id = _setup_admin()
    csrf = _login(client, username)
    dispatch = AsyncMock()
    with patch("app.services.sms_service.sms_available", return_value=True), patch(
        "app.services.sms_dispatch_service.dispatch_manual_sms", dispatch
    ):
        response = client.post(
            f"/kontakte/{kontakt_id}/sms",
            data={"_csrf": csrf, "telefon_id": telefon_id, "text": "Bitte rueckrufen"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/kontakte/{kontakt_id}?sms_started=")
    assert dispatch.await_count == 1
    args = dispatch.await_args.args
    assert args[3] == "Bitte rueckrufen"
    assert args[4] == {"+4366455555": ("SMS Kontakt", None, "kontakt", kontakt_id)}
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        log = db.query(SmsLog).filter_by(id=args[1]).one()
        assert log.recipient_count == 1 and log.source == "manual"
    finally:
        db.close()


def test_gateway_sms_lehnt_fremde_telefonnummer_ab(client):
    username, kontakt_id, _telefon_id = _setup_admin()
    _other_username, _other_kontakt_id, foreign_phone_id = _setup_admin()
    csrf = _login(client, username)
    response = client.post(
        f"/kontakte/{kontakt_id}/sms",
        data={"_csrf": csrf, "telefon_id": foreign_phone_id, "text": "Darf nicht raus"},
    )
    assert response.status_code == 404
