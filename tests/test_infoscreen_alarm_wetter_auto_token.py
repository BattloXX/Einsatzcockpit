"""Alarm-Infoscreen-Einstellungen: automatische Wetter-Dashboard-Token-Erzeugung
(app/routers/ui_infoscreen_alarm.py::einstellungen_speichern()).

Vorfall: Wetter im Ruhezustand wurde nicht dargestellt, weil die Wetter-URL
(enthaelt einen nur als Hash gespeicherten WeatherDashboardToken) manuell unter
/admin/settings/wetter erzeugt und hier hinterlegt werden musste - ein leicht
zu vergessender, komplett stumm fehlschlagender Zwischenschritt. Jetzt: wird
"Wetter" als Ruhezustand gewaehlt, ohne dass bereits eine URL hinterlegt ist,
erzeugt die Route den Token selbst.

Eigene, frische Org je Test (Muster: test_incident_duplicate_guard_ui.py)."""
import uuid

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings
from app.models.user import Role, User, UserRole
from app.models.weather import WeatherDashboardToken


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


def _setup_org_admin(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"infoscreen-wetter-{uuid.uuid4().hex[:8]}", name="Infoscreen-Wetter-Test-Org",
            color="#0088aa", bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, alarm_infoscreen_idle_modus="uhr"))
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Infoscreen Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "org_admin").id))
        db.commit()
        return org.id
    finally:
        db.close()


def test_wetter_ohne_url_erzeugt_automatisch_einen_token():
    org_id = _setup_org_admin("infoscreen_wetter_auto_user")

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "infoscreen_wetter_auto_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(
        "/infoscreen-alarm/verwaltung/einstellungen",
        data={
            "_csrf": csrf, "idle_modus": "wetter", "alarm_dauer_min": "60",
            "wetter_url": "", "gsl_enabled": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "saved_mit_neuem_wetter_token=1" in r.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        settings_row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        assert settings_row.alarm_infoscreen_idle_modus == "wetter"
        assert settings_row.alarm_infoscreen_wetter_url is not None
        assert "/wetter/infoscreen/" in settings_row.alarm_infoscreen_wetter_url

        tokens = db.query(WeatherDashboardToken).filter(WeatherDashboardToken.org_id == org_id).all()
        assert len(tokens) == 1
        assert tokens[0].label == "Alarm-Infoscreen (automatisch)"
        # Die im Klartext generierte Token-URL muss zum gespeicherten Hash passen.
        roh_token = settings_row.alarm_infoscreen_wetter_url.rsplit("/", 1)[-1]
        from app.core.security import hash_api_key
        assert tokens[0].token_hash == hash_api_key(roh_token)
    finally:
        db.close()


def test_wetter_mit_bereits_gesetzter_url_erzeugt_keinen_zweiten_token():
    org_id = _setup_org_admin("infoscreen_wetter_vorhanden_user")

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        settings_row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        settings_row.alarm_infoscreen_wetter_url = "https://example.com/wetter/infoscreen/bereits-vorhanden"
        db.commit()
    finally:
        db.close()

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "infoscreen_wetter_vorhanden_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(
        "/infoscreen-alarm/verwaltung/einstellungen",
        data={
            "_csrf": csrf, "idle_modus": "wetter", "alarm_dauer_min": "60",
            "wetter_url": "https://example.com/wetter/infoscreen/bereits-vorhanden", "gsl_enabled": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "saved_mit_neuem_wetter_token" not in r.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        assert db.query(WeatherDashboardToken).filter(WeatherDashboardToken.org_id == org_id).count() == 0
    finally:
        db.close()


def test_ruhezustand_uhr_ohne_wetter_url_erzeugt_keinen_token():
    """Kein Auto-Token, wenn "Wetter" gar nicht als Ruhezustand gewaehlt ist."""
    org_id = _setup_org_admin("infoscreen_wetter_uhr_user")

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "infoscreen_wetter_uhr_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(
        "/infoscreen-alarm/verwaltung/einstellungen",
        data={"_csrf": csrf, "idle_modus": "uhr", "alarm_dauer_min": "60", "wetter_url": "", "gsl_enabled": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "saved_mit_neuem_wetter_token" not in r.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        assert db.query(WeatherDashboardToken).filter(WeatherDashboardToken.org_id == org_id).count() == 0
    finally:
        db.close()
