"""Objektverwaltung PR 5: Alarm-Matching (BMA/Adresse/Geo) + Einsatz-Verknuepfung."""
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
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektBMA,
    ObjektEinsatz,
    ObjektZusatzadresse,
)
from app.services.objekt_matching_service import (
    _haversine_m,
    finde_bma_nummern,
    match_incident,
)


# ── BMA-Regex (parametrisiert, inkl. Negativfaelle) ───────────────────────────

@pytest.mark.parametrize("text,erwartet", [
    ("wolfurt dammstraße 64 bmz 1044 rattpack werk2 hat ausgelöst", ["1044"]),
    ("BMA-Nr.: 1044 Alarm", ["1044"]),
    ("BMA Nr 1044", ["1044"]),
    ("rfl/1044 ausgelöst", ["1044"]),
    ("BMZ:1044", ["1044"]),
    ("bmz 1044 und bmz 2088", ["1044", "2088"]),
    ("bmz 1044 bmz 1044", ["1044"]),  # dedupliziert
    ("Brand im Farbenlager, starke Rauchentwicklung", []),
    ("Umzug in die Bahnhofstraße 10", []),  # kein bmz/bma/rfl-Praefix
    (None, []),
    ("", []),
])
def test_bma_regex(text, erwartet):
    assert finde_bma_nummern(text) == erwartet


def test_haversine_grenzfaelle():
    assert _haversine_m(47.4652, 9.7503, 47.4652, 9.7503) == pytest.approx(0.0)
    # ~111 m pro 0.001° Breite
    d = _haversine_m(47.4652, 9.7503, 47.4662, 9.7503)
    assert 100 < d < 125


# ── In-Memory-Fixture ─────────────────────────────────────────────────────────

@pytest.fixture()
def match_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="m-org", name="Match Org", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="m-org-b", name="Fremde Org", color="#00ff00", bos="Feuerwehr")
    db.add_all([org, org_b])
    db.flush()
    db.add(OrgSettings(org_id=org.id, objekt_geo_match_radius_m=75))

    rattpack = Objekt(org_id=org.id, nummer=1, name="Rattpack Werk 2",
                      status=OBJEKT_STATUS_FREIGEGEBEN,
                      strasse="Dammstraße", hausnummer="64", ort="Wolfurt",
                      lat=47.46520, lng=9.75030)
    db.add(rattpack)
    db.flush()
    db.add(ObjektBMA(org_id=org.id, objekt_id=rattpack.id, bma_nummer="1044"))

    wohnanlage = Objekt(org_id=org.id, nummer=2, name="Wohnanlage Bahnhofstraße",
                        status=OBJEKT_STATUS_FREIGEGEBEN,
                        strasse="Bahnhofstraße", hausnummer="12", ort="Wolfurt",
                        lat=47.47000, lng=9.76000)
    db.add(wohnanlage)
    db.flush()
    db.add(ObjektZusatzadresse(org_id=org.id, objekt_id=wohnanlage.id,
                               bezeichnung="Stiege 2", strasse="Bahnhofstraße",
                               hausnummer="12a", ort="Wolfurt"))

    entwurf = Objekt(org_id=org.id, nummer=3, name="Entwurfsobjekt",
                     status=OBJEKT_STATUS_ENTWURF,
                     strasse="Testweg", hausnummer="1", ort="Wolfurt")
    db.add(entwurf)
    db.commit()

    yield db, org.id, org_b.id, rattpack, wohnanlage

    db.close()
    Base.metadata.drop_all(bind=engine)


def _incident(db, org_id, **kw):
    inc = Incident(
        primary_org_id=org_id,
        alarm_type_code=kw.get("alarm", "F14"),
        report_text=kw.get("report_text"),
        address_street=kw.get("street"),
        address_no=kw.get("no"),
        address_city=kw.get("city"),
        lat=kw.get("lat"),
        lng=kw.get("lng"),
    )
    db.add(inc)
    db.commit()
    return inc


def test_stufe1_bma_nummer(match_db):
    db, org_id, _, rattpack, _ = match_db
    inc = _incident(db, org_id, report_text="wolfurt dammstraße 64 bmz 1044 rattpack werk2 hat ausgelöst")
    neu = match_incident(db, inc)
    db.commit()
    assert len(neu) == 1
    assert neu[0].objekt_id == rattpack.id
    assert neu[0].quelle == "bma"
    assert neu[0].status == "bestaetigt"


def test_stufe2_adresse_inkl_zusatzadresse(match_db):
    db, org_id, _, _, wohnanlage = match_db
    inc = _incident(db, org_id, report_text="Brand Wohnung",
                    street="Bahnhofstraße", no="12a", city="Wolfurt")
    neu = match_incident(db, inc)
    db.commit()
    assert len(neu) == 1
    assert neu[0].objekt_id == wohnanlage.id
    assert neu[0].quelle == "adresse"
    assert neu[0].status == "bestaetigt"


def test_stufe3_geo_nur_vorschlag(match_db):
    db, org_id, _, rattpack, _ = match_db
    inc = _incident(db, org_id, report_text="Rauchentwicklung",
                    lat=47.46525, lng=9.75035)  # ~7 m vom Rattpack
    neu = match_incident(db, inc)
    db.commit()
    assert len(neu) == 1
    assert neu[0].objekt_id == rattpack.id
    assert neu[0].quelle == "geo"
    assert neu[0].status == "vorschlag"
    assert neu[0].distanz_m is not None and neu[0].distanz_m < 75


def test_geo_ausserhalb_radius_kein_match(match_db):
    db, org_id, _, _, _ = match_db
    inc = _incident(db, org_id, report_text="irgendwo", lat=47.480, lng=9.780)  # >1 km
    neu = match_incident(db, inc)
    assert neu == []


def test_bma_gewinnt_vor_adresse_und_geo(match_db):
    db, org_id, _, rattpack, _ = match_db
    inc = _incident(db, org_id,
                    report_text="bmz 1044 ausgelöst",
                    street="Bahnhofstraße", no="12", city="Wolfurt",
                    lat=47.47000, lng=9.76000)
    neu = match_incident(db, inc)
    db.commit()
    assert len(neu) == 1
    assert neu[0].objekt_id == rattpack.id
    assert neu[0].quelle == "bma"


def test_nur_geo_ueberspringt_stufe1_und_2(match_db):
    """nur_geo=True (nach Background-Geocoding): keine erneute BMA-/Adress-Pruefung."""
    db, org_id, _, rattpack, _ = match_db
    inc = _incident(db, org_id, report_text="bmz 1044", lat=47.46521, lng=9.75031)
    neu = match_incident(db, inc, nur_geo=True)
    db.commit()
    assert len(neu) == 1
    assert neu[0].quelle == "geo"


def test_geo_laeuft_nicht_wenn_schon_verknuepft(match_db):
    db, org_id, _, rattpack, wohnanlage = match_db
    inc = _incident(db, org_id, report_text="bmz 1044")
    match_incident(db, inc)
    db.commit()
    # Geocoding kommt spaeter → nur_geo-Lauf darf nichts hinzufuegen
    inc.lat, inc.lng = 47.47000, 9.76000  # nahe Wohnanlage
    neu = match_incident(db, inc, nur_geo=True)
    assert neu == []


def test_entwurf_wird_nicht_gematcht(match_db):
    db, org_id, _, _, _ = match_db
    inc = _incident(db, org_id, street="Testweg", no="1", city="Wolfurt")
    neu = match_incident(db, inc)
    assert neu == []


def test_kein_doppeltes_matching(match_db):
    db, org_id, _, _, _ = match_db
    inc = _incident(db, org_id, report_text="bmz 1044")
    erste = match_incident(db, inc)
    db.commit()
    zweite = match_incident(db, inc)
    assert len(erste) == 1
    assert zweite == []


def test_verknuepfung_isolation(match_db):
    db, org_id, org_b_id, _, _ = match_db
    inc = _incident(db, org_id, report_text="bmz 1044")
    match_incident(db, inc)
    db.commit()
    set_tenant_context(db, org_b_id)
    assert db.query(ObjektEinsatz).count() == 0
    set_tenant_context(db, None)
    assert db.query(ObjektEinsatz).count() == 1


def test_pr5_registrierung():
    from app.core.tenant import _TENANT_TABLE_NAMES
    assert "objekt_einsatz" in _TENANT_TABLE_NAMES
    from app.routers.ui_objekt import router
    pfade = {r.path for r in router.routes}
    assert "/objekte/einsatz-panel/{incident_id}" in pfade
    assert "/objekte/{objekt_id}/einsatz" in pfade
    assert "/objekte/{objekt_id}/einsaetze" in pfade
