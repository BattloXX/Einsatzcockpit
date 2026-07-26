"""Uebergreifendes Aenderungsprotokoll der Objektverwaltung (GET /objekte/changelog,
Gegenstueck zu protokoll_partial(), das nur ein einzelnes Objekt zeigt). Eigene,
frische Org je Test (Muster: test_incident_duplicate_guard_ui.py) statt der
geteilten Home-Org, damit Aenderungen anderer Tests nicht mit hineinspielen."""
import uuid

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektChange
from app.models.user import Role, User, UserRole


@pytest.fixture(autouse=True)
def _no_login_ratelimit():
    from app.core.rate_limit import limiter
    if limiter is None:
        yield
        return
    prev = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup_org_mit_user(username: str, rollen=("readonly",)) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"changelog-{uuid.uuid4().hex[:8]}", name="Changelog-Test-Org",
            color="#123456", bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Changelog Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))

        sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        db.add(OrgSettings(org_id=org.id, objekt_module_enabled=True))
        db.commit()
        return org.id
    finally:
        db.close()


def test_changelog_zeigt_aenderungen_ueber_mehrere_objekte():
    org_id = _setup_org_mit_user("changelog_user")

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        objekt_a = Objekt(org_id=org_id, nummer=1, name="Objekt A", status=OBJEKT_STATUS_FREIGEGEBEN)
        objekt_b = Objekt(org_id=org_id, nummer=2, name="Objekt B", status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add_all([objekt_a, objekt_b])
        db.flush()
        db.add(ObjektChange(
            org_id=org_id, objekt_id=objekt_a.id, bereich="stammdaten", feld="name",
            before_json='"Objekt A alt"', after_json='"Objekt A"',
        ))
        db.add(ObjektChange(
            org_id=org_id, objekt_id=objekt_b.id, bereich="bma", feld="bma_nummer",
            before_json=None, after_json='"1234"',
        ))
        db.commit()
        objekt_a_id, objekt_b_id = objekt_a.id, objekt_b.id
    finally:
        db.close()

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "changelog_user", "Test1234!")

    r = client.get("/objekte/changelog")
    assert r.status_code == 200
    assert "Änderungsprotokoll" in r.text
    assert "Objekt A" in r.text
    assert "Objekt B" in r.text
    assert f'/objekte/{objekt_a_id}"' in r.text
    assert f'/objekte/{objekt_b_id}"' in r.text
    assert "bma_nummer" in r.text


def test_changelog_ohne_aenderungen_zeigt_leeren_zustand():
    _setup_org_mit_user("changelog_leer_user")

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "changelog_leer_user", "Test1234!")

    r = client.get("/objekte/changelog")
    assert r.status_code == 200
    assert "Noch keine Änderungen protokolliert" in r.text


def test_changelog_tenant_isolation():
    """Aenderungen einer fremden Org duerfen im globalen Protokoll nicht auftauchen -
    der automatische Tenant-Filter (do_orm_execute) muss auch hier greifen."""
    org_a = _setup_org_mit_user("changelog_org_a_user")
    org_b = _setup_org_mit_user("changelog_org_b_user")

    db = SessionLocal()
    set_tenant_context(db, org_b)
    try:
        fremdes_objekt = Objekt(org_id=org_b, nummer=1, name="Fremdes Objekt", status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(fremdes_objekt)
        db.flush()
        db.add(ObjektChange(
            org_id=org_b, objekt_id=fremdes_objekt.id, bereich="stammdaten", feld="name",
            before_json=None, after_json='"Fremdes Objekt"',
        ))
        db.commit()
    finally:
        db.close()

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "changelog_org_a_user", "Test1234!")

    r = client.get("/objekte/changelog")
    assert r.status_code == 200
    assert "Fremdes Objekt" not in r.text
    assert org_a != org_b


def test_changelog_erreichbar_fuer_reine_lesekraft():
    """Anders als die BMA-Import-Review-Queue (nur objekt_verwalter) ist das
    globale Protokoll fuer ALLE Lese-Rollen zugaenglich (_LESE_ROLLEN, Muster
    Objektliste/protokoll_partial)."""
    _setup_org_mit_user("changelog_readonly_user", rollen=("readonly",))

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "changelog_readonly_user", "Test1234!")

    r = client.get("/objekte/changelog")
    assert r.status_code == 200
