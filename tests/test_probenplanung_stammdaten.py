"""Phase-1-Tests fuer Modul-Flag, Rollen und Probeart-CRUD."""
import re

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings, SystemSettings
from app.models.probenplanung import Probeart
from app.models.user import Role, User, UserRole

ORG_ID = 1


def _user(username: str, role_code: str) -> None:
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"), display_name=username, org_id=ORG_ID, active=True)
        db.add(user); db.flush()
        role = db.query(Role).filter(Role.code == role_code).one()
        db.add(UserRole(user_id=user.id, role_id=role.id)); db.commit()
    finally: db.close()


def _flags(enabled: bool) -> None:
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        row = db.query(SystemSettings).filter(SystemSettings.key == "probenplanung_module_enabled").first()
        if row is None: db.add(SystemSettings(key="probenplanung_module_enabled", value=str(enabled).lower()))
        else: row.value = str(enabled).lower()
        org = db.query(OrgSettings).filter(OrgSettings.org_id == ORG_ID).first()
        if org is None:
            org = OrgSettings(org_id=ORG_ID)
            db.add(org)
        org.probenplanung_modul_aktiv = enabled; db.commit()
    finally: db.close()


def _login(client, username: str) -> str:
    client.cookies.clear(); client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf})
    return client.cookies.get("ec_csrf")


def test_probeart_crud(client):
    _user("proben_admin", "org_admin"); _flags(True)
    csrf = _login(client, "proben_admin")
    response = client.post("/admin/probenplanung/probearten", data={
        "_csrf": csrf, "name": "Atemschutzprobe", "kurz": "ASP", "farbe": "#123abc",
        "sortierung": "55", "termin_typ": "uebung", "teilnahme_erforderlich": "1"},
        follow_redirects=False)
    assert response.status_code == 303
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        row = db.query(Probeart).filter(Probeart.name == "Atemschutzprobe").one(); row_id = row.id
    finally: db.close()
    response = client.post(f"/admin/probenplanung/probearten/{row_id}", data={
        "_csrf": csrf, "name": "Atemschutz-Vollprobe", "kurz": "ASV", "farbe": "#654321",
        "sortierung": "56", "termin_typ": "uebung", "aktiv": "1"}, follow_redirects=False)
    assert response.status_code == 303
    response = client.post(f"/admin/probenplanung/probearten/{row_id}/loeschen", data={"_csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303


def test_modul_aus_liefert_404(client):
    _user("proben_flag_admin", "org_admin"); _flags(False); _login(client, "proben_flag_admin")
    assert client.get("/admin/probenplanung/probearten").status_code == 404


def test_nur_org_admin_verwaltet_probearten(client):
    _user("proben_editor", "probenverwalter"); _flags(True); _login(client, "proben_editor")
    assert client.get("/admin/probenplanung/probearten").status_code == 403


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("role", ["org_admin", "probenverwalter", "readonly"])
def test_probeplan_navigation(client, enabled, role):
    username = f"nav_{role}_{enabled}"
    _user(username, role)
    _flags(enabled)
    _login(client, username)
    response = client.get("/probenplanung" if enabled else "/termine")
    assert response.status_code == 200
    links = re.findall(r'<a href="([^"]+)" class="([^"]*)">(🗓 Probeplan|📋 Teilnahme)</a>', response.text)
    # Je ein Eintrag im Desktop- und Mobile-Menue.
    assert len(links) == 2
    for href, classes, label in links:
        assert label == ("🗓 Probeplan" if enabled else "📋 Teilnahme")
        assert href == ("/probenplanung" if enabled else "/termine")
        assert "active" in classes.split()
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert ('class="module-btn__label">Teilnahme</span>' in dashboard.text) is not enabled


@pytest.mark.parametrize("page", ["probearten", "vorlagen", "oeffentlich"])
def test_admin_routing_and_legacy_redirect(client, page):
    username = f"routing_{page}"
    _user(username, "org_admin")
    _flags(True)
    _login(client, username)
    path = f"/admin/probenplanung/{page}"
    response = client.get(path)
    assert response.status_code == 200
    assert 'class="admin-shell"' in response.text
    assert "adminNav('verwaltung')" in response.text
    response = client.get(f"/probenplanung/verwaltung/{page}?test=1", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == path + "?test=1"
    _flags(False)
    assert client.get(path).status_code == 404
    assert client.get(f"/probenplanung/verwaltung/{page}").status_code == 404


@pytest.mark.parametrize("page", ["probearten", "vorlagen", "oeffentlich"])
@pytest.mark.parametrize("role", ["probenverwalter", "readonly"])
def test_admin_pages_require_org_admin(client, page, role):
    username = f"restricted_{page}_{role}"
    _user(username, role)
    _flags(True)
    _login(client, username)
    assert client.get(f"/admin/probenplanung/{page}").status_code == 403
    assert client.get(f"/probenplanung/verwaltung/{page}", follow_redirects=False).status_code == 403
