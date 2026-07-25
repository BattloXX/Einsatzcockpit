"""Objektverwaltung: Hinweis auf der Lagekarte für Stammdaten ohne Kartensymbol.

fehlende_kartensymbole() vergleicht BMA/Gefahren/Merkmale gegen bereits gesetzte
ObjektKartenObjekt-Marker und schlägt NUR vor (keine automatische Platzierung -
die Position lässt sich aus Text-Stammdaten nicht zuverlässig ermitteln).
"""
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektGefahr,
    ObjektKartenObjekt,
    ObjektMerkmal,
)
from app.services.objekt_service import fehlende_kartensymbole


@pytest.fixture()
def karte_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org = FireDept(slug="karte-hinweis-org", name="Karte-Hinweis-Org", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    db.add(GefahrenKatalog(org_id=org.id, name="Gasanschluss / Gasflaschen", piktogramm_typ="gas"))
    db.add(MerkmalKatalog(org_id=org.id, code="brandschutzplan", name="Brandschutzplan vorhanden"))
    db.add(MerkmalKatalog(org_id=org.id, code="tiefgarage", name="Tiefgarage"))  # kein Kartensymbol dafuer
    objekt = Objekt(org_id=org.id, nummer=1, name="Testobjekt", status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt)
    db.commit()

    yield db, org, objekt

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_leeres_objekt_keine_vorschlaege(karte_db):
    db, org, objekt = karte_db
    assert fehlende_kartensymbole(objekt) == []


def test_bmz_fbf_ohne_marker_werden_vorgeschlagen(karte_db):
    db, org, objekt = karte_db
    objekt.bma = ObjektBMA(org_id=org.id, objekt_id=objekt.id,
                           bmz_standort="BE1, UG (Feuerwehrzugang 1)", fbf_standort="BE1, UG BMZ")
    db.commit()
    db.refresh(objekt)

    vorschlaege = {v["typ"]: v for v in fehlende_kartensymbole(objekt)}
    assert vorschlaege["bmz"]["hinweis"] == "BE1, UG (Feuerwehrzugang 1)"
    assert vorschlaege["fbf"]["hinweis"] == "BE1, UG BMZ"


def test_bereits_gesetztes_symbol_wird_nicht_erneut_vorgeschlagen(karte_db):
    db, org, objekt = karte_db
    objekt.bma = ObjektBMA(org_id=org.id, objekt_id=objekt.id, bmz_standort="BE1, UG")
    db.add(ObjektKartenObjekt(org_id=org.id, objekt_id=objekt.id, typ="bmz", lat=47.1, lng=9.1))
    db.commit()
    db.refresh(objekt)

    typen = {v["typ"] for v in fehlende_kartensymbole(objekt)}
    assert "bmz" not in typen


def test_gefahr_ohne_marker_wird_vorgeschlagen(karte_db):
    db, org, objekt = karte_db
    gefahr = db.query(GefahrenKatalog).filter(GefahrenKatalog.piktogramm_typ == "gas").first()
    db.add(ObjektGefahr(org_id=org.id, objekt_id=objekt.id, gefahr_id=gefahr.id, detail="Sauerstofftank 2300l"))
    db.commit()
    db.refresh(objekt)

    vorschlaege = {v["typ"]: v for v in fehlende_kartensymbole(objekt)}
    assert vorschlaege["gefahr_gas"]["hinweis"] == "Sauerstofftank 2300l"


def test_merkmal_ohne_eindeutiges_symbol_wird_nicht_vorgeschlagen(karte_db):
    """'tiefgarage' hat kein 1:1-Kartensymbol - bewusst nicht vorschlagen statt
    ein falsches/mehrdeutiges Symbol zu erraten."""
    db, org, objekt = karte_db
    merkmal = db.query(MerkmalKatalog).filter(MerkmalKatalog.code == "tiefgarage").first()
    db.add(ObjektMerkmal(org_id=org.id, objekt_id=objekt.id, merkmal_id=merkmal.id))
    db.commit()
    db.refresh(objekt)

    assert fehlende_kartensymbole(objekt) == []


def test_merkmal_brandschutzplan_wird_vorgeschlagen(karte_db):
    db, org, objekt = karte_db
    merkmal = db.query(MerkmalKatalog).filter(MerkmalKatalog.code == "brandschutzplan").first()
    db.add(ObjektMerkmal(org_id=org.id, objekt_id=objekt.id, merkmal_id=merkmal.id))
    db.commit()
    db.refresh(objekt)

    typen = {v["typ"] for v in fehlende_kartensymbole(objekt)}
    assert "bsp" in typen
