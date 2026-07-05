"""Objektverwaltung PR 1: Feature-Flag, Guard, Modelle, Service, Isolation."""
from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite-Testumgebung
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept
from app.models.objekt import (
    OBJEKT_STATUS_ARCHIVIERT,
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_STATUS_UEBERARBEITUNG,
    Objekt,
    ObjektBMA,
    ObjektChange,
    ObjektKategorie,
)
from app.services.objekt_service import (
    aktualisiere_felder,
    berechne_vollstaendigkeit,
    naechste_nummer,
    objekt_effective_enabled,
    objekt_system_enabled,
    status_uebergang_erlaubt,
    write_objekt_change,
)


# ── Feature-Flag: Service-Logik (ohne HTTP) ───────────────────────────────────

class _Sys:
    def __init__(self, value=None):
        self.key = "objekt_module_enabled"
        self.value = value


class _OrgS:
    def __init__(self, enabled=False):
        self.objekt_module_enabled = enabled


def _db_with(sys_value, org_enabled):
    """Mock-DB: erster Query-Pfad SystemSettings, zweiter OrgSettings (mit execution_options)."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys(sys_value)
    db.query.return_value.filter.return_value.execution_options.return_value.first.return_value = _OrgS(org_enabled)
    return db


def test_system_flag_missing_returns_false():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert objekt_system_enabled(db) is False


def test_system_flag_false_value():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys("false")
    assert objekt_system_enabled(db) is False


def test_system_flag_true_value():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys("true")
    assert objekt_system_enabled(db) is True


def test_effective_false_when_no_org():
    db = MagicMock()
    assert objekt_effective_enabled(None, db) is False


def test_effective_false_when_system_off():
    db = _db_with("false", True)
    assert objekt_effective_enabled(1, db) is False


def test_effective_false_when_system_on_org_off():
    db = _db_with("true", False)
    assert objekt_effective_enabled(1, db) is False


def test_effective_true_when_both_on():
    db = _db_with("true", True)
    assert objekt_effective_enabled(1, db) is True


# ── Guard: HTTP 404 wenn nicht aktiv ──────────────────────────────────────────

def test_guard_404_when_module_off(client):
    """GET /objekte/ → 404 (Modul aus) oder 302 (Login-Redirect), nie 200."""
    resp = client.get("/objekte/", follow_redirects=False)
    assert resp.status_code in (302, 404)


# ── Importierbarkeit ──────────────────────────────────────────────────────────

def test_objekt_router_importable():
    from app.routers.ui_objekt import require_objekt_enabled, router
    assert callable(require_objekt_enabled)
    assert router is not None


def test_objekt_service_importable():
    assert callable(objekt_effective_enabled)
    assert callable(objekt_system_enabled)


def test_objekt_models_in_tenant_tables():
    from app.core.tenant import _TENANT_TABLE_NAMES
    for tbl in ("objekt", "objekt_kategorie", "objekt_zusatzadresse", "objekt_bma", "objekt_change"):
        assert tbl in _TENANT_TABLE_NAMES, f"{tbl} fehlt in _TENANT_TABLE_NAMES"


def test_objekt_modul_toggles_registriert():
    """Modul ist in den Einstellungen schaltbar: System-Toggle (system_admin)
    und Org-Toggle (Form-Feld im Org-Settings-POST)."""
    import inspect

    from app.routers.ui_settings import router as settings_router
    pfade = {r.path for r in settings_router.routes}
    assert "/admin/settings/system/objekt-toggle" in pfade
    from app.routers import ui_settings
    handler = next(
        r.endpoint for r in settings_router.routes
        if r.path == "/admin/settings/org" and "POST" in getattr(r, "methods", set())
    )
    params = inspect.signature(handler).parameters
    assert "objekt_module_enabled_raw" in params
    assert "objekt_geo_match_radius_raw" in params
    assert "objekt_ki_klassifikation_raw" in params
    assert ui_settings is not None


def test_objekt_verwalter_role_defined():
    from app.core.permissions import OBJEKT_VERWALTER_ROLES, ROLES
    assert ROLES["objekt_verwalter"] == 60
    assert "objekt_verwalter" in OBJEKT_VERWALTER_ROLES
    from app.seed_data import ROLES as SEED_ROLES
    assert any(r["code"] == "objekt_verwalter" for r in SEED_ROLES)


# ── Status-Workflow ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("von,nach,erlaubt", [
    (OBJEKT_STATUS_ENTWURF, OBJEKT_STATUS_FREIGEGEBEN, True),
    (OBJEKT_STATUS_ENTWURF, OBJEKT_STATUS_ARCHIVIERT, True),
    (OBJEKT_STATUS_ENTWURF, OBJEKT_STATUS_UEBERARBEITUNG, False),
    (OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_UEBERARBEITUNG, True),
    (OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_ENTWURF, False),
    (OBJEKT_STATUS_UEBERARBEITUNG, OBJEKT_STATUS_FREIGEGEBEN, True),
    (OBJEKT_STATUS_ARCHIVIERT, OBJEKT_STATUS_UEBERARBEITUNG, True),
    (OBJEKT_STATUS_ARCHIVIERT, OBJEKT_STATUS_FREIGEGEBEN, False),
])
def test_status_uebergaenge(von, nach, erlaubt):
    assert status_uebergang_erlaubt(von, nach) is erlaubt


# ── Vollstaendigkeit ──────────────────────────────────────────────────────────

def _objekt_stub(**kw):
    o = Objekt(
        nummer=1, name="Test", status=OBJEKT_STATUS_ENTWURF,
        strasse=kw.get("strasse"), ort=kw.get("ort"),
        lat=kw.get("lat"), lng=kw.get("lng"),
        kategorie_id=kw.get("kategorie_id"),
        revision_datum=kw.get("revision_datum"),
    )
    return o


def test_vollstaendigkeit_leer():
    v = berechne_vollstaendigkeit(_objekt_stub())
    assert v["prozent"] == 0
    assert "Adresse" in v["fehlend"]


def test_vollstaendigkeit_voll():
    o = _objekt_stub(strasse="Dammstraße", ort="Wolfurt", lat=47.4, lng=9.75,
                     kategorie_id=1, revision_datum=date(2027, 1, 1))
    v = berechne_vollstaendigkeit(o)
    assert v["prozent"] == 100
    assert v["fehlend"] == []


def test_vollstaendigkeit_bma_unvollstaendig():
    o = _objekt_stub(strasse="Dammstraße", ort="Wolfurt", lat=47.4, lng=9.75,
                     kategorie_id=1, revision_datum=date(2027, 1, 1))
    o.bma = ObjektBMA(objekt_id=1)  # ohne bma_nummer/bmz_standort
    v = berechne_vollstaendigkeit(o)
    assert v["prozent"] < 100
    assert "BMA-Details" in v["fehlend"]


# ── In-Memory-DB: Nummernvergabe, Change-Log, Tenant-Isolation ────────────────

@pytest.fixture(scope="module")
def objekt_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    org_a = FireDept(slug="obj-org-a", name="Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="obj-org-b", name="Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    kat_a = ObjektKategorie(org_id=org_a.id, name="Gewerbe/Industrie", sort=1)
    db.add(kat_a)
    db.add_all([
        Objekt(org_id=org_a.id, nummer=1, name="Objekt A1", status=OBJEKT_STATUS_FREIGEGEBEN),
        Objekt(org_id=org_a.id, nummer=2, name="Objekt A2", status=OBJEKT_STATUS_ENTWURF),
        Objekt(org_id=org_b.id, nummer=1, name="Objekt B1", status=OBJEKT_STATUS_FREIGEGEBEN),
    ])
    db.commit()

    yield db, org_a.id, org_b.id

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_nummernvergabe_je_org(objekt_db):
    db, org_a_id, org_b_id = objekt_db
    set_tenant_context(db, None)
    assert naechste_nummer(db, org_a_id) == 3
    assert naechste_nummer(db, org_b_id) == 2


def test_tenant_isolation_objekte(objekt_db):
    db, org_a_id, org_b_id = objekt_db
    set_tenant_context(db, org_a_id)
    objekte = db.query(Objekt).all()
    assert len(objekte) == 2
    assert all(o.org_id == org_a_id for o in objekte)

    set_tenant_context(db, org_b_id)
    objekte_b = db.query(Objekt).all()
    assert len(objekte_b) == 1
    assert objekte_b[0].name == "Objekt B1"


def test_tenant_isolation_kategorien(objekt_db):
    db, org_a_id, org_b_id = objekt_db
    set_tenant_context(db, org_b_id)
    assert db.query(ObjektKategorie).count() == 0
    set_tenant_context(db, org_a_id)
    assert db.query(ObjektKategorie).count() == 1


def test_change_log_wird_geschrieben(objekt_db):
    db, org_a_id, _ = objekt_db
    set_tenant_context(db, org_a_id)
    objekt = db.query(Objekt).filter(Objekt.nummer == 1).first()

    geaendert = aktualisiere_felder(
        db, objekt, {"name": "Objekt A1 neu", "ort": "Wolfurt"}, bereich="stammdaten", user_id=None
    )
    db.commit()

    assert set(geaendert) == {"name", "ort"}
    changes = db.query(ObjektChange).filter(ObjektChange.objekt_id == objekt.id).all()
    felder = {c.feld for c in changes}
    assert {"name", "ort"} <= felder
    name_change = next(c for c in changes if c.feld == "name")
    assert "Objekt A1" in (name_change.before_json or "")
    assert "Objekt A1 neu" in (name_change.after_json or "")

    # Keine Aenderung → kein neuer Eintrag
    anzahl_vorher = len(changes)
    geaendert2 = aktualisiere_felder(db, objekt, {"name": "Objekt A1 neu"}, bereich="stammdaten")
    db.commit()
    assert geaendert2 == []
    assert db.query(ObjektChange).filter(ObjektChange.objekt_id == objekt.id).count() == anzahl_vorher


def test_change_log_isolation(objekt_db):
    db, org_a_id, org_b_id = objekt_db
    set_tenant_context(db, org_a_id)
    objekt = db.query(Objekt).filter(Objekt.nummer == 1).first()
    write_objekt_change(db, objekt.id, org_a_id, "status", "status",
                        before="entwurf", after="freigegeben")
    db.commit()

    set_tenant_context(db, org_b_id)
    assert db.query(ObjektChange).count() == 0
