"""Integrationstests fuer die Planspiel-Uebersicht."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.planspiel import PlanspielMitglied, PlanspielRun, PlanspielSzenario
from app.models.user import AuditLog, Role, User, UserRole
from app.routers.ui_planspiel import require_planspiel_enabled


def test_modul_guard_liefert_404_wenn_deaktiviert():
    request = SimpleNamespace(state=SimpleNamespace(planspiel_enabled=False))
    with pytest.raises(HTTPException) as error:
        require_planspiel_enabled(request)
    assert error.value.status_code == 404


def _setup_user(role: str, *, org_id: int = 1, system_enabled: bool = True, org_enabled: bool = True) -> User:
    username = f"planspiel_{role}_{uuid4().hex}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        system = db.get(SystemSettings, "planspiel_module_enabled")
        if system_enabled:
            if system is None:
                db.add(SystemSettings(key="planspiel_module_enabled", value="true"))
            else:
                system.value = "true"
        elif system is not None:
            db.delete(system)
        settings = db.query(OrgSettings).filter_by(org_id=org_id).first() or OrgSettings(org_id=org_id)
        db.add(settings)
        settings.planspiel_modul_aktiv = org_enabled
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            org_id=org_id,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=role).one().id))
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> None:
    client.cookies.clear()
    client.get("/login")
    response = client.post(
        "/login", data={"username": user.username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")}
    )
    assert response.status_code == 200


def _run(org_id: int, titel: str, *, leiter_user_id: int | None = None) -> PlanspielRun:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        run = PlanspielRun(
            org_id=org_id,
            titel=titel,
            art="einsatz",
            erzeugung="manuell",
            status="entwurf",
            seed=1,
            leiter_user_id=leiter_user_id,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        db.expunge(run)
        return run
    finally:
        db.close()


def _other_org() -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"planspiel-{uuid4().hex}", name="Planspiel Org B", timezone="Europe/Vienna")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, planspiel_modul_aktiv=True))
        db.commit()
        return org.id
    finally:
        db.close()


def test_modulschalter_guard(client):
    system_off = _setup_user("uebungsleiter", system_enabled=False, org_enabled=True)
    _login(client, system_off)
    assert client.get("/planspiele").status_code == 404

    org_off = _setup_user("uebungsleiter", system_enabled=True, org_enabled=False)
    _login(client, org_off)
    assert client.get("/planspiele").status_code == 404

    enabled = _setup_user("uebungsleiter", system_enabled=True, org_enabled=True)
    _login(client, enabled)
    assert client.get("/planspiele").status_code == 200


def test_readonly_ist_nicht_berechtigt(client):
    reader = _setup_user("readonly")
    _login(client, reader)
    assert client.get("/planspiele").status_code == 403


def test_leiter_sieht_nur_runs_der_eigenen_org(client):
    leiter = _setup_user("uebungsleiter")
    own_title = f"Eigener Lauf {uuid4().hex}"
    foreign_title = f"Fremder Lauf {uuid4().hex}"
    _run(leiter.org_id, own_title, leiter_user_id=leiter.id)
    _run(_other_org(), foreign_title)
    _login(client, leiter)
    response = client.get("/planspiele")
    assert own_title in response.text
    assert foreign_title not in response.text


def test_beobachter_sieht_nur_eigene_mitgliedschaften(client):
    beobachter = _setup_user("uebungsbeobachter")
    visible_title = f"Sichtbar {uuid4().hex}"
    hidden_title = f"Unsichtbar {uuid4().hex}"
    visible = _run(beobachter.org_id, visible_title)
    _run(beobachter.org_id, hidden_title)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(
            PlanspielMitglied(
                org_id=beobachter.org_id, run_id=visible.id, user_id=beobachter.id, rolle="beobachter"
            )
        )
        db.commit()
    finally:
        db.close()
    _login(client, beobachter)
    response = client.get("/planspiele")
    assert visible_title in response.text
    assert hidden_title not in response.text


def test_navigation_erscheint_nur_bei_aktivem_modul(client):
    inactive = _setup_user("uebungsleiter", org_enabled=False)
    _login(client, inactive)
    assert "Planspiele" not in client.get("/").text

    active = _setup_user("uebungsleiter", org_enabled=True)
    _login(client, active)
    assert "Planspiele" in client.get("/").text


def test_org_toggle_schreibt_flag_und_audit(client):
    admin = _setup_user("org_admin", org_enabled=False)
    _login(client, admin)
    response = client.post(
        "/admin/settings/org",
        data={"_csrf": client.cookies.get("ec_csrf"), "planspiel_modul_aktiv_raw": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(OrgSettings).filter_by(org_id=admin.org_id).one().planspiel_modul_aktiv is True
        assert (
            db.query(PlanspielSzenario)
            .filter(PlanspielSzenario.org_id == admin.org_id, PlanspielSzenario.herkunft == "system")
            .count()
            == 57
        )
        assert (
            db.query(AuditLog)
            .filter(AuditLog.action == "planspiel.org_toggle", AuditLog.user_id == admin.id)
            .count()
            == 1
        )
    finally:
        db.close()
