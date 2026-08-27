"""End-to-End-Smoke-Test fuer das eigene Druckregeln-Tab.

Faengt genau die Fehlerklasse ab, die der Template-Umzug (detail.html ->
druckregeln.html + _gwx_style.html + _tabs.html) riskiert: kaputtes Include,
fehlender Makro-Import oder ein Context-Key, den die neue Route nicht mehr
liefert. Muster: tests/test_dibos_admin_routes.py.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import Gateway
from app.models.master import OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole
import pytest

ORG_ID = 1  # FF Wolfurt (seeded)


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


def _setup(username: str, rolle: str) -> int:
    """Legt Nutzer + aktives Gateway-Modul an und gibt die Gateway-ID zurueck."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        flag = db.query(SystemSettings).filter(
            SystemSettings.key == "gateway_module_enabled").first()
        if flag is None:
            db.add(SystemSettings(key="gateway_module_enabled", value="true"))
        else:
            flag.value = "true"
        org_s = db.query(OrgSettings).filter(OrgSettings.org_id == ORG_ID).first()
        if org_s is None:
            org_s = OrgSettings(org_id=ORG_ID)
            db.add(org_s)
        org_s.gateway_module_enabled = True

        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Druckregeln Test", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, rolle).id))

        gw = db.query(Gateway).filter(Gateway.org_id == ORG_ID).first()
        if gw is None:
            gw = Gateway(org_id=ORG_ID, name="Testgateway")
            db.add(gw)
            db.flush()
        db.commit()
        return gw.id
    finally:
        db.close()


def test_druckregeln_tab_rendert_und_detail_hat_die_regeln_nicht_mehr():
    gw_id = _setup("druckregeln_render_user", "org_admin")

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "druckregeln_render_user", "Test1234!")

    regeln = client.get(f"/gateway/{gw_id}/druckregeln")
    assert regeln.status_code == 200, regeln.text[:300]
    assert 'id="regeln"' in regeln.text
    assert "Druckregeln" in regeln.text
    # Tab-Leiste zeigt zurueck auf die Gateway-Seite
    assert f'href="/gateway/{gw_id}"' in regeln.text
    # gemeinsames Theme kommt aus dem ausgelagerten Style-Include
    assert ".gwx-tabs__item" in regeln.text

    detail = client.get(f"/gateway/{gw_id}")
    assert detail.status_code == 200, detail.text[:300]
    assert 'id="regeln"' not in detail.text
    assert f'href="/gateway/{gw_id}/druckregeln"' in detail.text
    # Die alte Verleih-Autodruck-Oberflaeche ist weg
    assert "Verleihschein automatisch drucken" not in detail.text


def test_druckregeln_tab_ist_fuer_recorder_gesperrt():
    gw_id = _setup("druckregeln_recorder_user", "recorder")

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "druckregeln_recorder_user", "Test1234!")

    r = client.get(f"/gateway/{gw_id}/druckregeln")
    assert r.status_code == 403, r.status_code


@pytest.mark.parametrize(
    ("query", "meldung"),
    [
        ("test_err=printer", "kein Zieldrucker"),
        ("test_err=gateway", "kein gekoppeltes Gateway"),
        ("test_err=einsatz", "keinen Einsatz"),
        ("test_err=gsl", "keine Großschadenslage"),
        ("test_err=verleih", "keinen Verleihschein"),
        ("test_err=alarm", "keinen seriellen Alarm"),
        ("test_err=leer&test_art=einsatz&test_ref=123", "keine Druckaufträge"),
    ],
)
def test_druckregeln_testmeldungen(query, meldung):
    gw_id = _setup("druckregeln_meldung_" + query.split("=")[1].split("&")[0], "org_admin")
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    _login(client, "druckregeln_meldung_" + query.split("=")[1].split("&")[0], "Test1234!")
    response = client.get(f"/gateway/{gw_id}/druckregeln?{query}")
    assert response.status_code == 200
    assert meldung in response.text
