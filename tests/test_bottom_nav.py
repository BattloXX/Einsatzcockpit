import uuid

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _setup_user(role_code: str) -> str:
    suffix = uuid.uuid4().hex[:10]
    username = f"bottom_nav_{suffix}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"bottom-nav-{suffix}", name="Bottom Nav Test")
        db.add(org)
        db.flush()
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="Bottom Nav Test",
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == role_code).one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return username
    finally:
        db.close()


def _login(client, username: str) -> None:
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _bottom_nav(html: str) -> str:
    start = html.index('<nav class="bottom-nav"')
    end = html.index("</nav>", start)
    return html[start:end]


def _mobile_nav(html: str) -> str:
    start = html.index('<nav class="mobile-menu__nav"')
    return html[start:html.index("</nav>", start)]


def test_incident_leader_sieht_fab(client):
    _login(client, _setup_user("incident_leader"))

    response = client.get("/")

    assert response.status_code == 200
    nav = _bottom_nav(response.text)
    assert "bottom-nav__fab" in nav
    assert 'aria-label="Neuen Einsatz starten"' in nav
    assert "neuenEinsatzOeffnen()" in nav
    assert nav.index("bottom-nav__fab") < nav.index('aria-label="Menü öffnen"')
    assert nav.index('aria-label="Menü öffnen"') < nav.index('aria-label="Profil und Konto"')
    assert '@click="mobileMenuOpen = true"' in nav


@pytest.mark.parametrize("role_code", ["readonly", "recorder"])
def test_nicht_admin_sieht_archiv_aber_keinen_fab(client, role_code):
    _login(client, _setup_user(role_code))

    response = client.get("/")
    nav = _bottom_nav(response.text)

    assert response.status_code == 200
    assert "bottom-nav__fab" not in nav
    assert "bottom-nav__item" in nav
    assert 'aria-label="Menü öffnen"' in nav
    assert '@click="mobileMenuOpen = true"' in nav
    assert 'href="/archiv"' not in nav
    assert 'href="/admin"' not in nav
    menue = _mobile_nav(response.text)
    assert 'href="/archiv"' in menue
    assert 'href="/admin"' not in menue


@pytest.mark.parametrize("role_code", ["admin", "org_admin", "system_admin", "fahrtenbuch_admin"])
def test_admin_sieht_verwaltung(client, role_code):
    _login(client, _setup_user(role_code))

    response = client.get("/")
    nav = _bottom_nav(response.text)

    assert response.status_code == 200
    assert 'aria-label="Menü öffnen"' in nav
    assert '@click="mobileMenuOpen = true"' in nav
    assert 'href="/admin"' not in nav
    menue = _mobile_nav(response.text)
    assert 'href="/admin"' in menue
    assert "Verwaltung" in menue
    assert 'href="/archiv"' in menue


def test_profil_sheet_vorhanden_und_wetter_nicht_in_bottom_nav(client):
    _login(client, _setup_user("incident_leader"))

    response = client.get("/")

    assert response.status_code == 200
    assert 'class="profil-sheet"' in response.text
    assert 'href="/wetter"' not in _bottom_nav(response.text)
