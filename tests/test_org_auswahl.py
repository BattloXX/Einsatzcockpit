"""Organisationslose Systemadmins landen nicht mehr in "Keine Organisation ausgewaehlt"."""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import Role, User, UserRole
from tests.test_systemstatus_nav import _login, _make_org_admin

SEITEN = [
    "/admin/settings",
    "/admin/settings/wetter",
    "/admin/settings/wetter/infoscreen",
    "/admin/sso",
    "/admin/dibos",
    "/admin/lis",
    "/admin/teams-alarmierung",
    "/admin/mail",
    "/admin/org-backup",
]


def _sysadmin_ohne_org(name: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=name, password_hash=hash_password("Test1234!"), display_name="Sys",
                    org_id=None, active=True)
        db.add(user)
        db.flush()
        rolle = db.query(Role).filter(Role.code == "system_admin").first()
        db.add(UserRole(user_id=user.id, role_id=rolle.id))
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize("pfad", SEITEN)
def test_sysadmin_ohne_org_wird_auf_organisation_weitergeleitet(client, setup_db, pfad):
    org_id = _make_org_admin(f"auswahl-oa-{pfad.strip('/').replace('/', '-')}", f"auswahl{abs(hash(pfad))}")
    name = f"auswahl-sys-{abs(hash(pfad))}"
    _sysadmin_ohne_org(name)
    _login(client, name, "Test1234!")
    antwort = client.get(pfad, follow_redirects=False)
    assert antwort.status_code == 303, (pfad, antwort.status_code)
    assert "org_id=" in antwort.headers["location"]
    assert antwort.headers["location"].startswith(pfad)
    ziel = client.get(f"{pfad}?org_id={org_id}", follow_redirects=False)
    assert ziel.status_code == 200, (pfad, ziel.status_code)


def test_org_admin_wird_nicht_weitergeleitet(client, setup_db):
    _make_org_admin("auswahl-orgadmin", "auswahl-orgadmin")
    _login(client, "auswahl-orgadmin", "Test1234!")
    assert client.get("/admin/settings/wetter", follow_redirects=False).status_code == 200



@pytest.mark.parametrize("pfad", ["/admin/settings/planspiele/szenarien", "/strassensperren/einstellungen"])
def test_organisationsmodule_sind_ohne_organisation_nicht_aktiv(client, setup_db, pfad):
    """Modul-Schalter haengen an den Org-Einstellungen: ohne Organisation 404 statt 400."""
    _sysadmin_ohne_org(f"auswahl-sys-modul-{abs(hash(pfad))}")
    _login(client, f"auswahl-sys-modul-{abs(hash(pfad))}", "Test1234!")
    assert client.get(pfad, follow_redirects=False).status_code == 404
