"""PR 6: PDF „Einsatzplan Wasserförderung" + Maschinisten-Token (public) + Dokumentart."""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.foerderstrecke import (
    FoerderPumpenTyp,
    FoerderSchlauchTyp,
    FoerderStation,
    Foerderstrecke,
)
from app.models.master import FireDept, OrgSettings, SystemSettings
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
        db.add(role); db.flush()
    return role


def _setup_strecke(username):
    """org_admin + aktives Modul + gespeicherte Strecke mit einer Quellpumpe."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="PDF Test", org_id=org.id, active=True)
        db.add(user); db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "org_admin").id))
        sys_row = db.get(SystemSettings, "foerderstrecke_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="foerderstrecke_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org.id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org.id); db.add(os_row)
        os_row.foerderstrecke_module_enabled = True

        pumpe = FoerderPumpenTyp(org_id=org.id, name="HLP-Test",
                                 kennlinien_json='{"2000": [[0, 53], [8000, 42], [16000, 18]]}')
        schlauch = FoerderSchlauchTyp(org_id=org.id, kuerzel="F-150", durchmesser_mm=150,
                                      k_verlust=0.049, element_laenge_m=30, wasserinhalt_l_m=17.7)
        db.add(pumpe); db.add(schlauch); db.flush()
        strecke = Foerderstrecke(org_id=org.id, name="PDF-Strecke",
                                 ansaug_json='{"seehoehe_m":430,"geodaetische_saughoehe_m":2}')
        db.add(strecke); db.flush()
        db.add(FoerderStation(org_id=org.id, strecke_id=strecke.id, sort=0, typ="quellpumpe",
                              lat=47.47, lng=9.75, pumpen_typ_id=pumpe.id, rpm="2000",
                              schlauch_typ_id=schlauch.id, druck_parallel=3,
                              abschnitt_laenge_m=500, abschnitt_delta_hoehe_m=10))
        db.commit()
        return org.id, strecke.id
    finally:
        db.close()


def test_pdf_smoke(client):
    _, sid = _setup_strecke("pdf_user")
    _login(client, "pdf_user", "Test1234!")
    r = client.get(f"/foerderstrecke/{sid}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"


def test_maschinisten_token_und_public_seite(client):
    _, sid = _setup_strecke("token_user")
    _login(client, "token_user", "Test1234!")
    client.get("/foerderstrecke/")
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/foerderstrecke/{sid}/maschinisten-token", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    url = r.json()["url"]
    assert "/m/foerderstrecke/" in url

    # Öffentliche Seite ohne Login (neuer Client ohne Session)
    from fastapi.testclient import TestClient

    from app.main import app
    pfad = "/m/foerderstrecke/" + url.split("/m/foerderstrecke/")[1]
    with TestClient(app) as anon:
        pub = anon.get(pfad)
        assert pub.status_code == 200
        assert "Maschinisten-Zettel" in pub.text
        assert "DBV" in pub.text


def test_ungueltiger_token_404(client):
    _setup_strecke("token_user2")
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/m/foerderstrecke/voellig-falscher-token").status_code == 404


def test_dokumentart_registriert():
    from app.models.objekt import DOKUMENTARTEN
    assert DOKUMENTARTEN.get("foerderstrecke") == "Einsatzplan Wasserförderung"
