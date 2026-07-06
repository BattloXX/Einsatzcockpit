"""Objektverwaltung PR 4: Objekt-Lagekarte (Kartenobjekte, GeoJSON, Isolation)."""
import json

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
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_SYMBOL_TYPEN,
    Objekt,
    ObjektKartenObjekt,
)


@pytest.fixture(scope="module")
def pr4_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org_a = FireDept(slug="pr4-org-a", name="Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="pr4-org-b", name="Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()
    objekt = Objekt(org_id=org_a.id, nummer=1, name="Kartenobjekt-Test",
                    status=OBJEKT_STATUS_FREIGEGEBEN, lat=47.4652, lng=9.7503)
    db.add(objekt)
    db.commit()

    yield db, org_a.id, org_b.id, objekt

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_symbolkatalog_vollstaendig():
    erwartet = {
        "fsd", "schluesselbox", "bsp", "bmz", "fbf", "dlk_stellplatz",
        "objektfunk", "sammelplatz", "feuerloescher", "hauptzugang",
        "nebenzugang", "stiege", "aufzug", "gefahr_ex", "gefahr_gas",
        "gefahr_chemie", "gefahr_strom", "gefahr_pv",
        "hydrant_ueberflur", "hydrant_unterflur",
    }
    assert set(OBJEKT_SYMBOL_TYPEN) == erwartet


def test_marker_crud(pr4_db):
    db, org_a_id, _, objekt = pr4_db
    set_tenant_context(db, org_a_id)
    marker = ObjektKartenObjekt(
        org_id=org_a_id, objekt_id=objekt.id, typ="fsd",
        lat=47.46521, lng=9.75032, label="beim Haupteingang", sort=1,
    )
    db.add(marker)
    db.commit()

    geladen = db.query(ObjektKartenObjekt).filter(ObjektKartenObjekt.typ == "fsd").first()
    assert geladen is not None
    assert geladen.label == "beim Haupteingang"

    geladen.lat = 47.46600
    db.commit()
    db.expire_all()
    assert db.query(ObjektKartenObjekt).filter(
        ObjektKartenObjekt.typ == "fsd").first().lat == pytest.approx(47.466)


def test_geometry_roundtrip(pr4_db):
    db, org_a_id, _, objekt = pr4_db
    set_tenant_context(db, org_a_id)
    polygon = {
        "type": "Polygon",
        "coordinates": [[[9.75, 47.465], [9.751, 47.465], [9.751, 47.466], [9.75, 47.465]]],
    }
    eintrag = ObjektKartenObjekt(
        org_id=org_a_id, objekt_id=objekt.id, typ="geometrie",
        geometry_json=json.dumps(polygon), label="Sammelplatz-Bereich",
    )
    db.add(eintrag)
    db.commit()
    db.expire_all()

    geladen = db.query(ObjektKartenObjekt).filter(
        ObjektKartenObjekt.typ == "geometrie").first()
    assert json.loads(geladen.geometry_json) == polygon

    from app.routers.ui_objekt import _karten_objekt_dict
    d = _karten_objekt_dict(geladen)
    assert d["geometry"]["type"] == "Polygon"
    assert d["lat"] is None


def test_eus_feature_wrapper_wird_ausgepackt():
    """EUS-Import speicherte GeoJSON als Feature-Wrapper -> geometry.type war
    'Feature' statt 'Point', wodurch Punkt-Symbole nicht als Icon rendern
    (Vorfall 2026-07-06). parse_karten_geometry muss das Feature auspacken."""
    from app.models.objekt import parse_karten_geometry

    feature_point = json.dumps({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [9.7478, 47.4618]},
        "properties": {"icon": "1", "featureType": "icon"},
    })
    geo = parse_karten_geometry(feature_point)
    assert geo == {"type": "Point", "coordinates": [9.7478, 47.4618]}

    feature_poly = json.dumps({
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[9, 47], [9.1, 47], [9.1, 47.1], [9, 47]]]},
        "properties": {},
    })
    assert parse_karten_geometry(feature_poly)["type"] == "Polygon"

    # Bereits bare Geometry bleibt unveraendert; Fehlerfaelle -> None
    assert parse_karten_geometry(json.dumps({"type": "Point", "coordinates": [1, 2]})) \
        == {"type": "Point", "coordinates": [1, 2]}
    assert parse_karten_geometry(None) is None
    assert parse_karten_geometry("{kaputt") is None


def test_eus_feature_punkt_dict_wird_als_punkt_erkannt(pr4_db):
    """Ein migrierter Punkt (Feature-gewrappt) muss im Frontend-Dict als Punkt
    (geometry.type == 'Point') ankommen, damit er als Symbol-Icon rendert."""
    db, org_a_id, _, objekt = pr4_db
    set_tenant_context(db, org_a_id)
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [9.7478, 47.4618]},
        "properties": {"icon": "6"},
    }
    eintrag = ObjektKartenObjekt(
        org_id=org_a_id, objekt_id=objekt.id, typ="bmz",
        lat=47.4618, lng=9.7478, geometry_json=json.dumps(feature), label="BMZ",
    )
    db.add(eintrag)
    db.commit()

    from app.routers.ui_objekt import _karten_objekt_dict
    d = _karten_objekt_dict(eintrag)
    assert d["geometry"]["type"] == "Point"  # nicht "Feature"
    assert d["typ"] == "bmz"
    set_tenant_context(db, None)


def test_karten_isolation(pr4_db):
    db, org_a_id, org_b_id, _ = pr4_db
    set_tenant_context(db, org_b_id)
    assert db.query(ObjektKartenObjekt).count() == 0
    set_tenant_context(db, org_a_id)
    assert db.query(ObjektKartenObjekt).count() >= 1
    set_tenant_context(db, None)


def test_kaskade_beim_objekt_loeschen(pr4_db):
    db, org_a_id, _, _ = pr4_db
    set_tenant_context(db, None)
    objekt2 = Objekt(org_id=org_a_id, nummer=2, name="Wegwerf",
                     status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt2)
    db.flush()
    db.add(ObjektKartenObjekt(org_id=org_a_id, objekt_id=objekt2.id, typ="bmz",
                              lat=47.0, lng=9.0))
    db.commit()

    db.delete(objekt2)
    db.commit()
    uebrig = db.query(ObjektKartenObjekt).filter(
        ObjektKartenObjekt.objekt_id == objekt2.id).count()
    assert uebrig == 0


def test_pr4_registrierung():
    from app.core.tenant import _TENANT_TABLE_NAMES
    assert "objekt_karten_objekt" in _TENANT_TABLE_NAMES
    from app.routers.ui_objekt import router
    pfade = {r.path for r in router.routes}
    assert "/objekte/{objekt_id}/karte" in pfade
    assert "/objekte/{objekt_id}/karte/objekte.json" in pfade
    assert "/objekte/{objekt_id}/karte/objekte" in pfade
    assert "/objekte/{objekt_id}/karte/einbettung" in pfade
