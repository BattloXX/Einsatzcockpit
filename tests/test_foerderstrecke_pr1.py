"""PR 1: Förderstrecken-Gerätekatalog — Feature-Flag, Modelle, Vorlagen, Admin-CRUD.

Deckt ab: zweistufiger Flag (System AND Org), Guard-404, Kennlinien-Validierung,
Wasserinhalt-Helper, Vorlagen-Katalog (TS 1600 & TS 1200), „Aus Vorlage anlegen"
erzeugt editierbare Org-Kopie, eigene Pumpe anlegen, Tenant-Isolation, JSON-Property.
"""
from unittest.mock import MagicMock

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.data.foerder_vorlagen import PUMPEN_VORLAGEN, SCHLAUCH_VORLAGEN
from app.db import SessionLocal
from app.models.foerderstrecke import (
    FoerderPumpenTyp,
    FoerderSchlauchTyp,
    wasserinhalt_pro_meter,
)
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole
from app.services.foerderstrecke_service import (
    foerderstrecke_effective_enabled,
    foerderstrecke_system_enabled,
    normalisiere_kennlinie_punkte,
)
from tests.conftest import TestingSession


# ── Feature-Flag (zweistufig, ohne HTTP) ──────────────────────────────────────

class _Sys:
    def __init__(self, value=None):
        self.key = "foerderstrecke_module_enabled"
        self.value = value


class _OrgS:
    def __init__(self, enabled=False):
        self.foerderstrecke_module_enabled = enabled


def test_system_flag_missing_returns_false():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert foerderstrecke_system_enabled(db) is False


def test_system_flag_true():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys("true")
    assert foerderstrecke_system_enabled(db) is True


def test_effective_false_when_no_org():
    assert foerderstrecke_effective_enabled(None, MagicMock()) is False


def test_effective_false_when_system_off():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys("false")
    assert foerderstrecke_effective_enabled(1, db) is False


def test_effective_false_when_system_on_org_off():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [_Sys("true"), _OrgS(False)]
    assert foerderstrecke_effective_enabled(1, db) is False


def test_effective_true_when_both_on():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [_Sys("true"), _OrgS(True)]
    assert foerderstrecke_effective_enabled(1, db) is True


# ── Kennlinien-Validierung ─────────────────────────────────────────────────────

def test_kennlinie_sortiert_und_akzeptiert_monoton():
    punkte, fehler = normalisiere_kennlinie_punkte(
        ["8000", "0", "5000"], ["42", "53", "48"])
    assert fehler == []
    assert punkte == [[0.0, 53.0], [5000.0, 48.0], [8000.0, 42.0]]


def test_kennlinie_negativ_wird_abgelehnt():
    _punkte, fehler = normalisiere_kennlinie_punkte(["-1"], ["10"])
    assert fehler


def test_kennlinie_nicht_monoton_wird_gemeldet():
    _punkte, fehler = normalisiere_kennlinie_punkte(["0", "5000"], ["10", "30"])
    assert any("monoton" in f for f in fehler)


def test_kennlinie_leere_zeilen_uebersprungen():
    punkte, fehler = normalisiere_kennlinie_punkte(["", "1000"], ["", "50"])
    assert fehler == []
    assert punkte == [[1000.0, 50.0]]


# ── Wasserinhalt-Helper ─────────────────────────────────────────────────────────

def test_wasserinhalt_f150_und_b75():
    assert abs(wasserinhalt_pro_meter(150) - 17.67) < 0.2   # Konzept: 17,7 l/m
    assert abs(wasserinhalt_pro_meter(75) - 4.42) < 0.1     # Konzept: 4,4 l/m
    assert wasserinhalt_pro_meter(0) is None
    assert wasserinhalt_pro_meter(None) is None


# ── Vorlagen-Katalog ────────────────────────────────────────────────────────────

def test_vorlagen_enthalten_ts1600_und_ts1200():
    assert "ts_1600_fox3" in PUMPEN_VORLAGEN
    assert "ts_1200" in PUMPEN_VORLAGEN
    for key in ("hlp_16000_pas200hf", "hlp_8000_pas150", "fremdpumpe_fpn"):
        assert key in PUMPEN_VORLAGEN
    assert {"f_150", "a_110", "b_75"} <= set(SCHLAUCH_VORLAGEN.keys())


def test_vorlagen_kennlinien_valide():
    """Jede Pumpen-Vorlage hat mindestens eine Kennlinienstufe mit Punkten."""
    for key, v in PUMPEN_VORLAGEN.items():
        kl = v["felder"].get("kennlinien") or {}
        assert kl, f"{key} ohne Kennlinie"
        for stufe, punkte in kl.items():
            assert punkte and all(len(p) == 2 for p in punkte), f"{key}/{stufe}"


# ── Modell-Roundtrip + JSON-Property (SQLite) ──────────────────────────────────

@pytest.fixture
def db_ctx():
    db = TestingSession()
    set_tenant_context(db, None)
    yield db
    db.rollback()
    db.close()


def test_pumpe_roundtrip_und_kennlinien_property(db_ctx):
    db = db_ctx
    p = FoerderPumpenTyp(
        org_id=770001,
        name="Test-Pumpe",
        kennlinien_json='{"2000": [[0, 53], [8000, 30]], "nenn": [[0, 20]]}',
    )
    db.add(p)
    db.flush()
    got = db.get(FoerderPumpenTyp, p.id)
    assert got.kennlinien["2000"] == [[0, 53], [8000, 30]]
    # Sortierung: numerische Stufe vor 'nenn'
    assert got.drehzahlstufen == ["2000", "nenn"]


def test_kennlinien_property_fallback_bei_kaputtem_json(db_ctx):
    db = db_ctx
    p = FoerderPumpenTyp(org_id=770002, name="Kaputt", kennlinien_json="{nicht json")
    db.add(p)
    db.flush()
    assert p.kennlinien == {}
    assert p.drehzahlstufen == []


def test_schlauch_wasserinhalt_gesetzt(db_ctx):
    db = db_ctx
    s = FoerderSchlauchTyp(
        org_id=770003, kuerzel="B-75", durchmesser_mm=75, k_verlust=1.56,
        wasserinhalt_l_m=wasserinhalt_pro_meter(75),
    )
    db.add(s)
    db.flush()
    assert abs(s.wasserinhalt_l_m - 4.42) < 0.1


def test_tenant_isolation(db_ctx):
    db = db_ctx
    db.add(FoerderPumpenTyp(org_id=880001, name="Org-A-Pumpe"))
    db.add(FoerderPumpenTyp(org_id=880002, name="Org-B-Pumpe"))
    db.flush()
    set_tenant_context(db, 880001)
    sichtbar = db.query(FoerderPumpenTyp).all()
    assert sichtbar and all(p.org_id == 880001 for p in sichtbar)
    set_tenant_context(db, None)  # zurücksetzen für Teardown


# ── HTTP: Guard + Admin-CRUD + Vorlagen ─────────────────────────────────────────

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


def _setup_user(username, *, module_on=True):
    """Legt org_admin-User an und setzt System-/Org-Flag der Förderstrecke."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Förder Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "org_admin").id))

        sys_row = db.get(SystemSettings, "foerderstrecke_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="foerderstrecke_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org.id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org.id)
            db.add(os_row)
        os_row.foerderstrecke_module_enabled = bool(module_on)
        db.commit()
        return org.id
    finally:
        db.close()


def test_guard_404_wenn_org_flag_aus(client):
    _setup_user("foerder_guard_user", module_on=False)
    _login(client, "foerder_guard_user", "Test1234!")
    r = client.get("/admin/foerderpumpen", follow_redirects=False)
    assert r.status_code == 404


def test_liste_leer_wenn_modul_an(client):
    _setup_user("foerder_liste_user", module_on=True)
    _login(client, "foerder_liste_user", "Test1234!")
    r = client.get("/admin/foerderpumpen")
    assert r.status_code == 200
    assert "Pumpen" in r.text


def _csrf(client):
    client.get("/admin/foerderpumpen")
    return client.cookies.get("ec_csrf")


def test_aus_vorlage_erzeugt_editierbare_org_kopie(client):
    org_id = _setup_user("foerder_vorlage_user", module_on=True)
    _login(client, "foerder_vorlage_user", "Test1234!")
    for key in ("ts_1600_fox3", "ts_1200"):
        r = client.post(f"/admin/foerderpumpen/aus-vorlage/{key}",
                        data={"_csrf": _csrf(client)}, follow_redirects=False)
        assert r.status_code == 303
        assert "/bearbeiten" in r.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kopien = (db.query(FoerderPumpenTyp)
                  .filter(FoerderPumpenTyp.org_id == org_id,
                          FoerderPumpenTyp.quelle == "vorlage").all())
        keys = {p.vorlage_key for p in kopien}
        assert "ts_1600_fox3" in keys and "ts_1200" in keys
        ts1600 = next(p for p in kopien if p.vorlage_key == "ts_1600_fox3")
        # Kennlinie wurde mitkopiert und ist editierbar (org-eigene Zeile)
        assert ts1600.kennlinien
        assert ts1600.org_id == org_id
    finally:
        db.close()


def test_eigene_pumpe_anlegen(client):
    org_id = _setup_user("foerder_neu_user", module_on=True)
    _login(client, "foerder_neu_user", "Test1234!")
    r = client.post("/admin/foerderpumpen/neu", data={
        "_csrf": _csrf(client),
        "name": "Eigene TS",
        "kennlinien_json": '{"2000": [[0, 50], [8000, 30]]}',
        "druck_anschluss_dn": "75", "druck_parallel_max": "2",
        "saug_anschluss_dn": "110", "saug_parallel_max": "1",
        "max_ansaughoehe_m": "7.5", "min_eingangsdruck_bar": "1.5",
    }, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        p = (db.query(FoerderPumpenTyp)
             .filter(FoerderPumpenTyp.org_id == org_id, FoerderPumpenTyp.name == "Eigene TS")
             .first())
        assert p is not None
        assert p.quelle == "manuell"
        assert p.kennlinien["2000"] == [[0, 50], [8000, 30]]
    finally:
        db.close()


def test_neu_lehnt_ungueltige_kennlinie_ab(client):
    _setup_user("foerder_invalid_user", module_on=True)
    _login(client, "foerder_invalid_user", "Test1234!")
    r = client.post("/admin/foerderpumpen/neu", data={
        "_csrf": _csrf(client),
        "name": "Kaputte Kennlinie",
        # H steigt mit Q → nicht monoton fallend → Fehler
        "kennlinien_json": '{"2000": [[0, 10], [5000, 30]]}',
    }, follow_redirects=False)
    assert r.status_code == 400
    assert "monoton" in r.text or "korrigieren" in r.text.lower()


def test_schlauch_aus_vorlage(client):
    org_id = _setup_user("foerder_schlauch_user", module_on=True)
    _login(client, "foerder_schlauch_user", "Test1234!")
    client.get("/admin/foerderschlaeuche")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/admin/foerderschlaeuche/aus-vorlage/b_75",
                    data={"_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        s = (db.query(FoerderSchlauchTyp)
             .filter(FoerderSchlauchTyp.org_id == org_id, FoerderSchlauchTyp.kuerzel == "B-75")
             .first())
        assert s is not None
        assert s.quelle == "vorlage"
        assert abs(s.wasserinhalt_l_m - 4.42) < 0.1
    finally:
        db.close()
