"""Kernlogik fuer externe Objekt-Pflegeauftraege."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.kontakt import Kontakt
from app.models.master import FireDept
from app.models.objekt import (
    OBJEKT_STATUS_ARCHIVIERT,
    OBJEKT_STATUS_FREIGEGEBEN,
    PFLEGEAUFTRAG_STATUS_FREIGEGEBEN,
    PFLEGEAUFTRAG_STATUS_IN_BEARBEITUNG,
    PFLEGEAUFTRAG_STATUS_WIDERRUFEN,
    Objekt,
    ObjektKontakt,
    ObjektPflegeEreignis,
    ObjektPflegeauftrag,
)
from app.services.objekt_pflege_service import (
    erstelle_pflegeauftrag,
    hole_oder_erstelle_arbeitskopie_fuer_auftrag,
    hole_pflegeauftrag_by_token,
    pflegeauftrag_status_wechsel,
    pflegeauftrag_token_gueltig,
    verwirf_pflegeauftrag_aenderungen,
    wende_externe_feldaenderungen_an,
)
from app.services.objekt_service import aktualisiere_felder, erstelle_arbeitskopie, uebernimm_arbeitskopie


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def _daten(db, suffix="A"):
    org = FireDept(slug=f"pflege-{suffix}", name=f"Pflege {suffix}", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    set_tenant_context(db, org.id)
    objekt = Objekt(org_id=org.id, nummer=1, name="Alt", status=OBJEKT_STATUS_FREIGEGEBEN)
    kontakt = Kontakt(org_id=org.id, anzeigename="Kontakt", email="kontakt@example.test")
    db.add_all([objekt, kontakt])
    db.flush()
    db.add(ObjektKontakt(org_id=org.id, objekt_id=objekt.id, kontakt_id=kontakt.id, art="betreiber"))
    db.commit()
    return org, objekt, kontakt


def test_token_lookup_is_cross_tenant_but_normal_query_is_scoped(db):
    org_a, objekt_a, kontakt_a = _daten(db, "A")
    auftrag, raw = erstelle_pflegeauftrag(db, objekt_a, kontakt_a, ersteller_id=None, bereiche=["stammdaten"])
    db.commit()
    org_b, _, _ = _daten(db, "B")
    assert hole_pflegeauftrag_by_token(db, raw).org_id == org_a.id
    assert db.query(ObjektPflegeauftrag).filter(ObjektPflegeauftrag.id == auftrag.id).first() is None
    assert org_b.id != org_a.id


def test_gueltigkeit_status_und_statuswechsel(db):
    _, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    assert pflegeauftrag_token_gueltig(auftrag)
    auftrag.gueltig_bis = datetime.now(UTC) - timedelta(seconds=1)
    assert not pflegeauftrag_token_gueltig(auftrag)
    auftrag.gueltig_bis = datetime.now(UTC) + timedelta(days=1)
    auftrag.status = PFLEGEAUFTRAG_STATUS_IN_BEARBEITUNG
    pflegeauftrag_status_wechsel(db, auftrag, "eingereicht")
    pflegeauftrag_status_wechsel(db, auftrag, PFLEGEAUFTRAG_STATUS_FREIGEGEBEN, user_id=12)
    assert auftrag.freigegeben_am is not None
    assert auftrag.freigegeben_von_id == 12
    assert db.query(ObjektPflegeEreignis).filter(ObjektPflegeEreignis.typ == "freigegeben").first()
    with pytest.raises(ValueError):
        pflegeauftrag_status_wechsel(db, auftrag, PFLEGEAUFTRAG_STATUS_WIDERRUFEN)


def test_auftrag_validiert_kontakt_kopie_und_offenen_auftrag(db):
    _, objekt, kontakt = _daten(db)
    kontakt.email = ""
    with pytest.raises(ValueError, match="E-Mail"):
        erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    kontakt.email = "kontakt@example.test"
    fremder = Kontakt(org_id=objekt.org_id, anzeigename="Fremd", email="fremd@example.test")
    db.add(fremder)
    db.flush()
    with pytest.raises(ValueError, match="zugeordnet"):
        erstelle_pflegeauftrag(db, objekt, fremder, ersteller_id=None, bereiche=["stammdaten"])
    kopie = erstelle_arbeitskopie(db, objekt, user_id=None)
    with pytest.raises(ValueError, match="Arbeitskopie"):
        erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    db.delete(kopie)
    db.flush()
    objekt.status = OBJEKT_STATUS_FREIGEGEBEN
    erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    with pytest.raises(ValueError, match="Pflegeauftrag"):
        erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])


def test_archiviertes_objekt_kann_keinen_pflegeauftrag_erhalten(db):
    _, objekt, kontakt = _daten(db)
    objekt.status = OBJEKT_STATUS_ARCHIVIERT

    with pytest.raises(ValueError, match="Archivierte Objekte"):
        erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])


def test_arbeitskopie_und_verwerfen_erhaelt_interne_aenderung(db):
    _, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    kopie = hole_oder_erstelle_arbeitskopie_fuer_auftrag(db, auftrag)
    assert hole_oder_erstelle_arbeitskopie_fuer_auftrag(db, auftrag).id == kopie.id
    wende_externe_feldaenderungen_an(db, auftrag, kopie, {"name": "Extern"}, kontakt_id=kontakt.id)
    aktualisiere_felder(db, kopie, {"vulgoname": "Intern"}, bereich="stammdaten", user_id=1)
    db.flush()
    verwirf_pflegeauftrag_aenderungen(db, auftrag, user_id=1)
    assert db.get(Objekt, kopie.id) is kopie
    assert kopie.name == "Alt"
    assert kopie.vulgoname == "Intern"


def test_reine_externe_kopie_wird_verworfen(db):
    _, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    kopie = hole_oder_erstelle_arbeitskopie_fuer_auftrag(db, auftrag)
    wende_externe_feldaenderungen_an(db, auftrag, kopie, {"name": "Extern"}, kontakt_id=kontakt.id)
    db.flush()
    verwirf_pflegeauftrag_aenderungen(db, auftrag, user_id=None)
    db.flush()
    assert db.get(Objekt, kopie.id) is None


def test_extern_uebernommene_arbeitskopie_erzeugt_spaeter_neue_kopie(db):
    """Die Router-Sperre verhindert diesen inkonsistenten Altpfad vor dem Merge.

    Der niedrigschwellige Service bleibt bewusst unveraendert: Nach dem direkten Merge
    einer externen Kopie ist ihre gespeicherte ID tot und ein Folgezugriff erstellt eine
    neue Arbeitskopie des nun produktiven Stands.
    """
    _, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    kopie = hole_oder_erstelle_arbeitskopie_fuer_auftrag(db, auftrag)
    wende_externe_feldaenderungen_an(db, auftrag, kopie, {"name": "Extern"}, kontakt_id=kontakt.id)
    db.flush()

    uebernimm_arbeitskopie(db, kopie, user_id=1)
    db.flush()
    alte_kopie_id = kopie.id
    assert db.get(Objekt, alte_kopie_id) is None
    assert objekt.name == "Extern"

    neue_kopie = hole_oder_erstelle_arbeitskopie_fuer_auftrag(db, auftrag)
    assert neue_kopie is not kopie
    # SQLite darf die gerade freigewordene Primärschlüssel-ID wiederverwenden.
    assert neue_kopie.id == alte_kopie_id
    assert auftrag.arbeitskopie_id == neue_kopie.id
    assert neue_kopie.name == "Extern"
