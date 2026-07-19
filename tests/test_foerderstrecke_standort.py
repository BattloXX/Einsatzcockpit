"""Modus B: Empfehlung der Pumpenstandorte aus Route + gewählten Pumpen."""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.foerderstrecke import FoerderPumpenTyp, FoerderSchlauchTyp
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole
from app.services.foerderstrecke_service import Ansaugpunkt, standort_vorschlag

KL = [[0, 53], [8000, 42], [16000, 18]]        # HLP-artig
K_F150 = 0.049
K_B75 = 1.56


# ── Engine ───────────────────────────────────────────────────────────────────

def test_flache_f150_strecke_braucht_keine_relais():
    res = standort_vorschlag(2000, KL, KL, K_F150, 1000, n_parallel=1)
    assert res["machbar"] is True
    assert res["n_relais"] == 0
    assert res["n_gesamt"] == 1


def test_lange_b75_strecke_braucht_relais():
    res = standort_vorschlag(2000, KL, KL, K_B75, 1000, n_parallel=1)
    assert res["n_relais"] >= 3
    # Standorte streng aufsteigend entlang der Strecke
    s = [st["s_m"] for st in res["standorte"]]
    assert s == sorted(s) and s[0] == 0.0


def test_ziel_q_ueber_kennlinie_warnt():
    res = standort_vorschlag(500, KL, KL, K_F150, 20000)
    assert any("Kennlinien-Maximum" in w for w in res["warnungen"])


def test_zu_hohe_q_fuer_b75_nicht_machbar():
    # 4000 l/min über B-75: Reibung je 25-m-Segment > verfügbarer Pumpendruck →
    # selbst eine frische Relaispumpe kommt keinen Abschnitt weit.
    res = standort_vorschlag(500, KL, KL, K_B75, 4000, n_parallel=1)
    assert res["machbar"] is False
    assert res["warnungen"]


def test_schwache_relaispumpe_setzt_keine_sinnlose_pumpe():
    # Starke Quellpumpe, aber Relaispumpe liefert bei der Ziel-Fördermenge nur ~1 bar
    # (< 1,5 bar Mindest-Eingangsdruck). Früher wurde trotzdem eine gebündelte, nutzlose
    # Relaispumpe dicht hinter der Quellpumpe gesetzt (Standort „macht keinen Sinn").
    quelle = [[0, 53], [8000, 42], [16000, 18]]        # stark
    relais = [[0, 15], [8000, 11], [16000, 5]]         # schwach: H(8000)=11 m = 1,1 bar
    res = standort_vorschlag(900, quelle, relais, K_F150, 8000, n_parallel=1)
    assert res["machbar"] is False
    assert res["n_relais"] == 0                         # KEINE sinnlose Verstärkerpumpe
    assert res["n_gesamt"] == 1                         # nur die Quellpumpe
    assert any("Relaispumpe liefert" in w for w in res["warnungen"])


def test_steiler_damm_mit_vielen_relais_oder_unmoeglich():
    # +100 m in einem 25-m-Segment (Wand) → nicht überwindbar
    profil = [[0, 400], [100, 400], [125, 500], [500, 500]]
    res = standort_vorschlag(500, KL, KL, K_B75, 1000, hoehenprofil=profil)
    assert res["machbar"] is False


def test_saugseite_grenzwertig_warnt():
    ansaug = Ansaugpunkt(geodaetische_saughoehe_m=2.0, saug_scheitel_m=9.0, max_ansaughoehe_m=7.5)
    res = standort_vorschlag(300, KL, KL, K_F150, 1000, ansaug=ansaug)
    assert any("Saug" in w for w in res["warnungen"])


# ── Endpoint ─────────────────────────────────────────────────────────────────

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
    r = db.query(Role).filter(Role.code == code).first()
    if r is None:
        r = Role(code=code, name=code); db.add(r); db.flush()
    return r


def _setup(username):
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="MB", org_id=org.id, active=True)
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
        schlauch = FoerderSchlauchTyp(org_id=org.id, kuerzel="B-75", durchmesser_mm=75,
                                      k_verlust=1.56, element_laenge_m=20, wasserinhalt_l_m=4.4)
        db.add(pumpe); db.add(schlauch); db.commit()
        return pumpe.id, schlauch.id
    finally:
        db.close()


def test_endpoint_standort_vorschlag(client):
    pid, sid = _setup("mb_ep")
    _login(client, "mb_ep", "Test1234!")
    client.get("/foerderstrecke/")
    csrf = client.cookies.get("ec_csrf")
    # ~1 km Route (0.009° lat ≈ 1000 m)
    route = [[47.40, 9.70], [47.409, 9.70]]
    r = client.post("/foerderstrecke/standort-vorschlag", json={
        "route": route, "ziel_q_l_min": 1000,
        "quelle_pumpe_id": pid, "quelle_rpm": "2000",
        "relais_pumpe_id": pid, "relais_rpm": "2000",
        "schlauch_typ_id": sid, "n_parallel": 1,
        "ansaug": {"seehoehe_m": 430, "geodaetische_saughoehe_m": 2},
    }, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["n_gesamt"] >= 2                         # Quelle + mind. 1 Relais (B-75, 1 km)
    assert len(d["stationen"]) == d["n_gesamt"]
    # jede Station hat Koordinaten auf der Route
    assert all(st["lat"] is not None and st["lng"] is not None for st in d["stationen"])
    assert d["stationen"][0]["typ"] == "quellpumpe"
