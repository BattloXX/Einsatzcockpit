"""Objektverwaltung: Arbeitskopie-Workflow ueber HTTP (Routen-Verdrahtung).

Ergaenzt tests/test_objekt_arbeitskopie.py (Service-Ebene) um den HTTP-Pfad:
POST /{id}/ueberarbeiten|uebernehmen|verwerfen, den Guard in status_wechseln, und
dass die Detailseite fuer objekt_verwalter transparent auf die Arbeitskopie umschaltet
(_objekt_arbeitsstand), waehrend Nicht-Verwalter immer den produktiven Stand sehen.
"""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt
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


def _setup_objekt(username, *, nummer, rollen=("objekt_verwalter",)):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Routentest", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))

        sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org.id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org.id)
            db.add(os_row)
        os_row.objekt_module_enabled = True

        obj = Objekt(org_id=org.id, nummer=nummer, name="Routen-Test-Objekt",
                     status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(obj)
        db.commit()
        return org.id, obj.id
    finally:
        db.close()


def _csrf(client):
    return client.cookies.get("ec_csrf")


def test_ueberarbeiten_uebernehmen_zyklus(client):
    _, obj_id = _setup_objekt("ak_zyklus_user", nummer=8801)
    _login(client, "ak_zyklus_user", "Test1234!")

    # Vor der Ueberarbeitung: normale freigegebene Detailseite.
    r = client.get(f"/objekte/{obj_id}")
    assert r.status_code == 200 and "Sie bearbeiten eine" not in r.text

    # Ueberarbeiten → Detailseite zeigt transparent die Arbeitskopie.
    r = client.post(f"/objekte/{obj_id}/ueberarbeiten",
                    data={"_csrf": _csrf(client)}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/objekte/{obj_id}")
    assert r.status_code == 200
    assert "Sie bearbeiten eine" in r.text
    assert "Überarbeitung freigeben" in r.text

    # Waehrend der Ueberarbeitung bleibt die produktive Version explizit abrufbar.
    r = client.get(f"/objekte/{obj_id}?fassung=produktiv")
    assert r.status_code == 200
    assert "Sie bearbeiten eine" not in r.text
    assert "läuft gerade eine Überarbeitung" in r.text

    # Uebernehmen (Merge) → id bleibt stabil, Status wieder freigegeben, kein Banner mehr.
    r = client.post(f"/objekte/{obj_id}/uebernehmen",
                    data={"_csrf": _csrf(client)}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/objekte/{obj_id}")
    assert r.status_code == 200
    assert "Sie bearbeiten eine" not in r.text
    assert "läuft gerade eine Überarbeitung" not in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.query(Objekt).filter(Objekt.id == obj_id).first()
        assert objekt.status == OBJEKT_STATUS_FREIGEGEBEN
        assert objekt.entwurf_von_id is None
    finally:
        db.close()


def test_verwerfen_stellt_produktive_version_wieder_her(client):
    _, obj_id = _setup_objekt("ak_verwerfen_user", nummer=8802)
    _login(client, "ak_verwerfen_user", "Test1234!")

    client.post(f"/objekte/{obj_id}/ueberarbeiten", data={"_csrf": _csrf(client)}, follow_redirects=False)
    r = client.post(f"/objekte/{obj_id}/verwerfen",
                    data={"_csrf": _csrf(client)}, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.query(Objekt).filter(Objekt.id == obj_id).first()
        assert objekt.status == OBJEKT_STATUS_FREIGEGEBEN
        assert db.query(Objekt).filter(Objekt.entwurf_von_id == obj_id).first() is None
    finally:
        db.close()


def test_status_route_lehnt_arbeitskopie_ab(client):
    """Der generische Statuswechsel darf eine Arbeitskopie nicht direkt auf
    'freigegeben' setzen - das wuerde den Merge umgehen (siehe status_wechseln-Guard)."""
    _, obj_id = _setup_objekt("ak_status_guard_user", nummer=8803)
    _login(client, "ak_status_guard_user", "Test1234!")

    client.post(f"/objekte/{obj_id}/ueberarbeiten", data={"_csrf": _csrf(client)}, follow_redirects=False)

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kopie = db.query(Objekt).filter(Objekt.entwurf_von_id == obj_id).first()
        assert kopie is not None
        kopie_id = kopie.id
    finally:
        db.close()

    r = client.post(f"/objekte/{kopie_id}/status",
                    data={"_csrf": _csrf(client), "neuer_status": "freigegeben"})
    assert r.status_code == 400


def test_nicht_verwalter_sieht_immer_produktive_version(client):
    _, obj_id = _setup_objekt("ak_verwalter_user", nummer=8804)
    _login(client, "ak_verwalter_user", "Test1234!")
    client.post(f"/objekte/{obj_id}/ueberarbeiten", data={"_csrf": _csrf(client)}, follow_redirects=False)
    client.get("/logout", follow_redirects=False)

    _setup_objekt_leser("ak_leser_user", org_username="ak_verwalter_user")
    _login(client, "ak_leser_user", "Test1234!")
    r = client.get(f"/objekte/{obj_id}")
    assert r.status_code == 200
    assert "Sie bearbeiten eine" not in r.text


def _setup_objekt_leser(username, *, org_username):
    """Legt einen readonly-User in derselben Org wie `org_username` an."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vorhanden = db.query(User).filter(User.username == org_username).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Leser Test", org_id=vorhanden.org_id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "readonly").id))
        db.commit()
    finally:
        db.close()
