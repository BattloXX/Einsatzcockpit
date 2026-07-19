"""PR 4: Karten-Wizard + Live-Berechnung + Profil-SVG + Persistenz-Routen."""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.foerderstrecke import (
    FoerderPumpenTyp,
    FoerderSchlauchTyp,
    Foerderstrecke,
)
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole
from app.services.chart_svg import foerderprofil_svg
from app.services.foerderstrecke_service import materialbilanz


# ── Pure: Materialbilanz + SVG ───────────────────────────────────────────────

def test_materialbilanz():
    ab = [
        {"kuerzel": "F-150", "laenge_m": 1000, "n_parallel": 3, "element_laenge_m": 30, "wasserinhalt_l_m": 17.7},
        {"kuerzel": "F-150", "laenge_m": 200, "n_parallel": 1, "element_laenge_m": 30, "wasserinhalt_l_m": 17.7},
    ]
    m = materialbilanz(ab, q_l_min=4000, reserve=0.10)
    f150 = next(s for s in m["schlaeuche"] if s["kuerzel"] == "F-150")
    assert f150["meter"] == 3200.0                 # 3*1000 + 200
    assert f150["meter_mit_reserve"] == 3520.0
    assert f150["elemente"] == 118                 # ceil(3520/30)
    assert m["wasservolumen_l"] > 0
    assert m["fuellzeit_min"] is not None


def test_materialbilanz_float_grenzfall():
    # 900 · 1,1 = 990,0000001 (Float) darf NICHT auf 34 aufrunden
    m = materialbilanz(
        [{"kuerzel": "F-150", "laenge_m": 300, "n_parallel": 3, "element_laenge_m": 30}],
        q_l_min=12000, reserve=0.10)
    assert m["schlaeuche"][0]["meter_mit_reserve"] == 990.0
    assert m["schlaeuche"][0]["elemente"] == 33


def test_profil_svg_grenzen_und_hochpunkt():
    svg = foerderprofil_svg([(0, 5.3), (500, 2.0), (1000, 0.3)], p_max_bar=15,
                            hochpunkt_min_bar=0.5, titel="X")
    assert svg.startswith("<svg") and "Min 1,5 bar" in svg and "Max 15 bar" in svg
    assert "#dc2626" in svg                        # Hochpunkt-Marker (0,3 < 0,5)
    assert "Keine Berechnungsdaten" in foerderprofil_svg([])


# ── HTTP-Setup ───────────────────────────────────────────────────────────────

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


def _setup(username, *, module_on=True):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="FS Test", org_id=org.id, active=True)
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
        os_row.foerderstrecke_module_enabled = bool(module_on)
        # Katalog: eine Pumpe + ein Schlauch
        pumpe = FoerderPumpenTyp(org_id=org.id, name="HLP-Test",
                                 kennlinien_json='{"2000": [[0, 53], [8000, 42], [16000, 18]]}',
                                 druck_anschluss_dn=150, druck_parallel_max=3)
        schlauch = FoerderSchlauchTyp(org_id=org.id, kuerzel="F-150", durchmesser_mm=150,
                                      k_verlust=0.049, element_laenge_m=30, wasserinhalt_l_m=17.7)
        db.add(pumpe); db.add(schlauch); db.commit()
        return org.id, pumpe.id, schlauch.id
    finally:
        db.close()


def _csrf(client):
    client.get("/foerderstrecke/")
    return client.cookies.get("ec_csrf")


# ── Guard + Render ───────────────────────────────────────────────────────────

def test_guard_404_wenn_aus(client):
    _setup("fs_ui_guard", module_on=False)
    _login(client, "fs_ui_guard", "Test1234!")
    assert client.get("/foerderstrecke/", follow_redirects=False).status_code == 404


def test_wizard_rendert(client):
    _setup("fs_ui_render", module_on=True)
    _login(client, "fs_ui_render", "Test1234!")
    r = client.get("/foerderstrecke/")
    assert r.status_code == 200
    assert "Förderstrecken-Planer" in r.text
    assert "fs-map" in r.text and "foerderPlaner" in r.text


# ── Berechnung ───────────────────────────────────────────────────────────────

def test_berechnen_inline(client):
    _setup("fs_ui_calc", module_on=True)
    _login(client, "fs_ui_calc", "Test1234!")
    csrf = _csrf(client)
    payload = {
        "name": "Test",
        "ansaug": {"seehoehe_m": 430, "geodaetische_saughoehe_m": 2, "saug_k": 0.23},
        "ziel_druck_bar": 0,
        "stationen": [{
            "typ": "quellpumpe",
            "kennlinie": [[0, 53], [8000, 42], [16000, 18]],
            "abschnitt": {"schlauch_k": 0.049, "schlauch_kuerzel": "F-150",
                          "laenge_m": 500, "n_parallel": 3, "element_laenge_m": 30,
                          "wasserinhalt_l_m": 17.7, "delta_hoehe_m": 0},
        }],
    }
    r = client.post("/foerderstrecke/berechnen", json=payload, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["machbar"] is True
    assert d["q_max_l_min"] > 1000
    assert d["svg"].startswith("<svg")
    assert d["material"]["schlaeuche"]


def test_berechnen_mit_katalog_ids(client):
    _, pid, sid = _setup("fs_ui_katalog", module_on=True)
    _login(client, "fs_ui_katalog", "Test1234!")
    csrf = _csrf(client)
    payload = {
        "ansaug": {"seehoehe_m": 430, "geodaetische_saughoehe_m": 2},
        "stationen": [{"typ": "quellpumpe", "pumpen_typ_id": pid, "rpm": "2000",
                       "abschnitt": {"schlauch_typ_id": sid, "laenge_m": 400, "n_parallel": 2,
                                     "delta_hoehe_m": 0}}],
    }
    r = client.post("/foerderstrecke/berechnen", json=payload, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text[:300]
    assert r.json()["machbar"] is True


def test_hoehenprofil_endpoint(client, monkeypatch):
    _setup("fs_ui_hoehe", module_on=True)
    _login(client, "fs_ui_hoehe", "Test1234!")
    csrf = _csrf(client)

    async def _stub(route, segment_m=25.0, db=None):
        return {"stuetzpunkte": [{"s_m": 0, "lat": route[0][0], "lng": route[0][1], "hoehe_m": 430.0},
                                 {"s_m": 100, "lat": route[-1][0], "lng": route[-1][1], "hoehe_m": 445.0}],
                "quelle": "openmeteo", "grob": True}
    import app.services.hoehen_service as hs
    monkeypatch.setattr(hs, "hoehenprofil", _stub)

    r = client.post("/foerderstrecke/hoehenprofil",
                    json={"route": [[47.0, 9.0], [47.001, 9.0]]}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    d = r.json()
    assert len(d["stuetzpunkte"]) == 2 and d["grob"] is True


# ── Speichern + Laden ────────────────────────────────────────────────────────

def test_speichern_und_laden(client):
    org_id, pid, sid = _setup("fs_ui_save", module_on=True)
    _login(client, "fs_ui_save", "Test1234!")
    csrf = _csrf(client)
    payload = {
        "name": "Meine Strecke",
        "ansaug": {"seehoehe_m": 430, "geodaetische_saughoehe_m": 2},
        "stationen": [{"typ": "quellpumpe", "pumpen_typ_id": pid, "rpm": "2000",
                       "abschnitt": {"schlauch_typ_id": sid, "n_parallel": 3, "laenge_m": 500}}],
    }
    r = client.post("/foerderstrecke/speichern", json=payload, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text[:300]
    sid_saved = r.json()["id"]

    db = SessionLocal(); set_tenant_context(db, None)
    try:
        s = db.get(Foerderstrecke, sid_saved)
        assert s is not None and s.org_id == org_id
        assert len(s.stationen) == 1
        assert s.stationen[0].pumpen_typ_id == pid
    finally:
        db.close()

    # Laden rendert den Wizard mit der Strecke
    r2 = client.get(f"/foerderstrecke/{sid_saved}")
    assert r2.status_code == 200
    assert "Meine Strecke" in r2.text


def test_berechnen_druckspeisung_hydrant(client):
    """Druckgespeiste erste Pumpe (Hydrant): Vordruck wird berücksichtigt."""
    _setup("fs_ui_hydrant", module_on=True)
    _login(client, "fs_ui_hydrant", "Test1234!")
    csrf = _csrf(client)
    payload = {
        "ansaug": {"druckspeisung": True, "eingangsdruck_bar": 4.0},
        "stationen": [{
            "typ": "quellpumpe",
            "kennlinie": [[0, 53], [8000, 42], [16000, 18]],
            "abschnitt": {"schlauch_k": 0.049, "laenge_m": 400, "n_parallel": 2, "delta_hoehe_m": 0},
        }],
    }
    r = client.post("/foerderstrecke/berechnen", json=payload, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["machbar"] is True
    assert d["stationswerte"][0]["p_ein_bar"] == 4.0     # Vordruck als Eingangsdruck


def test_speichern_mit_lage_verknuepfung(client):
    """Förderstrecke lässt sich mit einer Lage verbinden (lage_id persistiert + geladen)."""
    from app.models.major_incident import MajorIncident, MajorIncidentStatus

    org_id, pid, sid = _setup("fs_ui_lage", module_on=True)
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        lage = MajorIncident(org_id=org_id, name="Hochwasser Testbach",
                             status=MajorIncidentStatus.active)
        db.add(lage); db.commit()
        lage_id = lage.id
    finally:
        db.close()

    _login(client, "fs_ui_lage", "Test1234!")
    csrf = _csrf(client)
    payload = {
        "name": "Versorgung Testbach", "lage_id": lage_id,
        "ansaug": {"seehoehe_m": 430, "geodaetische_saughoehe_m": 2},
        "stationen": [{"typ": "quellpumpe", "pumpen_typ_id": pid, "rpm": "2000",
                       "abschnitt": {"schlauch_typ_id": sid, "laenge_m": 400}}],
    }
    r = client.post("/foerderstrecke/speichern", json=payload, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text[:300]
    strecke_id = r.json()["id"]

    db = SessionLocal(); set_tenant_context(db, None)
    try:
        s = db.get(Foerderstrecke, strecke_id)
        assert s.lage_id == lage_id
    finally:
        db.close()

    # Lage-Board zeigt die verknüpfte Förderstrecke als Auftrag mit Link
    r2 = client.get(f"/lage/{lage_id}")
    assert r2.status_code == 200
    assert "Versorgung Testbach" in r2.text
    assert f"/foerderstrecke/{strecke_id}" in r2.text


def test_wasserstellen_layer_json(client):
    """Wasserstellen-Layer liefert aktive Stellen der Org mit Koordinaten."""
    from app.models.wasserstelle import Wasserstelle

    org_id, _pid, _sid = _setup("fs_ui_ws", module_on=True)
    ids = []
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        # weit weg von Wolfurt, damit andere (radiusbasierte) Tests nicht verfälscht werden
        rows = [
            Wasserstelle(org_id=org_id, bezeichnung="FS-Test Hydrant", typ="hydrant",
                         lat=49.9, lng=14.9, quelle="manuell", status="bereit", aktiv=True),
            Wasserstelle(org_id=org_id, bezeichnung="FS-Test ohne Koord", typ="hydrant",
                         quelle="manuell", status="bereit", aktiv=True),
            Wasserstelle(org_id=org_id, bezeichnung="FS-Test inaktiv", typ="hydrant",
                         lat=49.91, lng=14.91, quelle="manuell", status="bereit", aktiv=False),
        ]
        for w in rows:
            db.add(w)
        db.commit()
        ids = [w.id for w in rows]
    finally:
        db.close()

    try:
        _login(client, "fs_ui_ws", "Test1234!")
        r = client.get("/foerderstrecke/wasserstellen.json")
        assert r.status_code == 200
        namen = [w["bezeichnung"] for w in r.json()["wasserstellen"]]
        assert "FS-Test Hydrant" in namen
        assert "FS-Test ohne Koord" not in namen    # ohne lat/lng ausgefiltert
        assert "FS-Test inaktiv" not in namen        # inaktiv ausgefiltert
    finally:
        # Session-DB wird nicht je Test zurückgerollt → eigene Testdaten wieder entfernen
        db = SessionLocal(); set_tenant_context(db, None)
        try:
            for wid in ids:
                obj = db.get(Wasserstelle, wid)
                if obj is not None:
                    db.delete(obj)
            db.commit()
        finally:
            db.close()


def test_standort_vorschlag_liegt_auf_route(client):
    """Vorgeschlagene Pumpenstandorte liegen auf der Förderleitung (nicht daneben)."""
    _, pid, sid = _setup("fs_ui_onroute", module_on=True)
    _login(client, "fs_ui_onroute", "Test1234!")
    csrf = _csrf(client)
    # L-förmige Route: nach Osten, dann nach Norden
    route = [[47.47, 9.75], [47.47, 9.775], [47.48, 9.775]]
    payload = {
        "route": route, "ziel_q_l_min": 800,
        "ansaug": {"seehoehe_m": 430, "geodaetische_saughoehe_m": 2},
        "quelle_pumpe_id": pid, "quelle_rpm": "2000",
        "relais_pumpe_id": pid, "relais_rpm": "2000",
        "schlauch_typ_id": sid, "n_parallel": 2,
    }
    r = client.post("/foerderstrecke/standort-vorschlag", json=payload, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text[:300]
    stationen = r.json()["stationen"]
    assert stationen
    # jeder Standort liegt (nahezu) auf einem Segment der Route
    for st in stationen:
        auf_route = any(_punkt_nahe_segment(st["lat"], st["lng"], route[i], route[i + 1])
                        for i in range(len(route) - 1))
        assert auf_route, f"Standort {st['lat']},{st['lng']} liegt nicht auf der Route"


def _punkt_nahe_segment(lat, lng, a, b, tol=1e-4):
    import math
    kx = math.cos(math.radians(a[0])) * 111320.0
    ky = 110574.0
    px, py = (lng - a[1]) * kx, (lat - a[0]) * ky
    bx, by = (b[1] - a[1]) * kx, (b[0] - a[0]) * ky
    seg2 = bx * bx + by * by
    if seg2 == 0:
        return math.hypot(px, py) < tol * ky
    t = max(0.0, min(1.0, (px * bx + py * by) / seg2))
    d = math.hypot(px - t * bx, py - t * by)
    return d < 5.0     # < 5 m vom Segment
