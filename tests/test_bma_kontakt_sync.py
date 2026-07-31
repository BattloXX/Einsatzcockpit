"""Regressionstests fuer den BMA-Kontaktabgleich innerhalb einer Session."""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.bma_import import BmaImportSatz, OrgBmaImportConfig
from app.models.master import FireDept
from app.models.objekt import OBJEKT_STATUS_ENTWURF, Objekt, ObjektBMA, ObjektKontakt
from app.services.bma_import.bma_sync import _sync_kontakte, verarbeite_pdf_anlage


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    org = FireDept(slug=f"bma-sync-{uuid.uuid4().hex[:8]}", name="BMA Sync", color="#123456", bos="Feuerwehr")
    session.add(org)
    session.flush()
    set_tenant_context(session, org.id)
    yield session, org
    session.close()
    Base.metadata.drop_all(bind=engine)


def _objekt(db, org, *, name="BMA Test"):
    objekt = Objekt(org_id=org.id, nummer=1, name=name, status=OBJEKT_STATUS_ENTWURF)
    db.add(objekt)
    db.flush()
    return objekt


def _satz(db, org, objekt, extern_id="pdf:1332"):
    satz = BmaImportSatz(org_id=org.id, objekt_id=objekt.id, extern_id=extern_id)
    db.add(satz)
    db.flush()
    return satz


def _kontakt(extern_id, name="Max Muster", telefone=None):
    return {"extern_id": extern_id, "name": name, "art": "bma_alarmperson",
            "telefone": telefone or ["+43 555 123"]}


def test_import_adoptiert_haendisch_gepflegten_kontakt_statt_zu_duplizieren(db):
    session, org = db
    objekt = _objekt(session, org)
    hand = ObjektKontakt(org_id=org.id, objekt_id=objekt.id, art="bma_alarmperson",
                         name="Max Muster", erreichbarkeit="Mo-Fr 8-17", sort=1)
    objekt.kontakte.append(hand)
    session.flush()
    hand_id = hand.id

    _sync_kontakte(session, _satz(session, org, objekt), objekt,
                    [_kontakt("pdf:1332:bma_alarmperson:max-muster")], None)

    assert len(objekt.kontakte) == 1
    assert hand.id == hand_id
    assert hand.extern_quelle == "dibos_bma"
    assert hand.erreichbarkeit == "Mo-Fr 8-17"
    assert hand.telefone == ["+43 555 123"]


def test_adoption_matcht_ueber_namensnormalisierung(db):
    session, org = db
    objekt = _objekt(session, org)
    hand = ObjektKontakt(org_id=org.id, objekt_id=objekt.id, art="bma_alarmperson",
                         name="Andreas B\u00f6hler", sort=1)
    objekt.kontakte.append(hand)
    session.flush()
    hand_id = hand.id

    _sync_kontakte(session, _satz(session, org, objekt), objekt,
                    [_kontakt("pdf:1332:bma_alarmperson:andreas-boehler", "Andreas Boehler")], None)

    assert len(objekt.kontakte) == 1
    assert objekt.kontakte[0].id == hand_id


def test_gleiches_datenblatt_zweimal_in_einer_session_legt_jeden_kontakt_einmal_an(db):
    session, org = db
    objekt = _objekt(session, org)
    objekt.bma = ObjektBMA(org_id=org.id, objekt_id=objekt.id, bma_nummer="1332")
    config = OrgBmaImportConfig(org_id=org.id, auto_anlegen=True)
    session.add(config)
    anlage = {"extern_id": "pdf:1332", "bezeichnung": objekt.name, "bma_nummer": "1332"}
    kontakte = [_kontakt("pdf:1332:bma_alarmperson:max-muster"),
                _kontakt("pdf:1332:bma_alarmperson:eva-beispiel", "Eva Beispiel")]
    user = SimpleNamespace(id=None, org_id=org.id)

    verarbeite_pdf_anlage(session, org.id, config, anlage, kontakte, user)
    verarbeite_pdf_anlage(session, org.id, config, anlage, kontakte, user)

    assert session.query(ObjektKontakt).filter(ObjektKontakt.objekt_id == objekt.id).count() == len(kontakte)


def test_zwei_datenblaetter_am_selben_objekt_bleiben_disjunkt(db):
    session, org = db
    objekt = _objekt(session, org)
    _sync_kontakte(session, _satz(session, org, objekt, "pdf:1332"), objekt,
                    [_kontakt("pdf:1332:bma_alarmperson:max-muster")], None)
    _sync_kontakte(session, _satz(session, org, objekt, "pdf:1338"), objekt,
                    [_kontakt("pdf:1338:bma_alarmperson:max-muster")], None)

    assert len(objekt.kontakte) == 2
    assert {k.extern_id for k in objekt.kontakte} == {
        "pdf:1332:bma_alarmperson:max-muster", "pdf:1338:bma_alarmperson:max-muster"}


def test_verschobener_kollisionszaehler_rekeyt_zeile_statt_sie_zu_ersetzen(db):
    session, org = db
    objekt = _objekt(session, org)
    alt = ObjektKontakt(org_id=org.id, objekt_id=objekt.id, art="bma_alarmperson",
                        name="Max Muster", extern_quelle="dibos_bma",
                        extern_id="pdf:1332:bma_alarmperson:max-muster#2",
                        erreichbarkeit="nachts", sort=1)
    objekt.kontakte.append(alt)
    session.flush()
    alt_id = alt.id

    _sync_kontakte(session, _satz(session, org, objekt), objekt,
                    [_kontakt("pdf:1332:bma_alarmperson:max-muster")], None)

    assert len(objekt.kontakte) == 1
    assert alt.id == alt_id
    assert alt.extern_id == "pdf:1332:bma_alarmperson:max-muster"
    assert alt.erreichbarkeit == "nachts"


def test_geaenderter_name_mit_neuem_slug_ersetzt_die_zeile(db):
    session, org = db
    objekt = _objekt(session, org)
    alt = ObjektKontakt(org_id=org.id, objekt_id=objekt.id, art="bma_alarmperson",
                        name="Max Muster", extern_quelle="dibos_bma",
                        extern_id="pdf:1332:bma_alarmperson:max-muster", sort=1)
    objekt.kontakte.append(alt)
    session.flush()
    alt_id = alt.id

    _sync_kontakte(session, _satz(session, org, objekt), objekt,
                    [_kontakt("pdf:1332:bma_alarmperson:maximilian-muster", "Maximilian Muster")], None)

    assert len(objekt.kontakte) == 1
    assert objekt.kontakte[0].id != alt_id
    assert objekt.kontakte[0].name == "Maximilian Muster"


def test_doppelte_extern_id_in_einer_kontaktliste_legt_nur_eine_zeile_an(db):
    session, org = db
    objekt = _objekt(session, org)
    extern_id = "pdf:1332:bma_alarmperson:max-muster"

    _sync_kontakte(session, _satz(session, org, objekt), objekt,
                    [_kontakt(extern_id), _kontakt(extern_id, telefone=["+43 555 999"])], None)

    assert len(objekt.kontakte) == 1
    assert objekt.kontakte[0].telefone == ["+43 555 999"]


def test_kontakt_der_aus_dem_datenblatt_verschwindet_wird_entfernt(db):
    session, org = db
    objekt = _objekt(session, org)
    kontakt = ObjektKontakt(org_id=org.id, objekt_id=objekt.id, art="bma_alarmperson",
                            name="Max Muster", extern_quelle="dibos_bma",
                            extern_id="pdf:1332:bma_alarmperson:max-muster", sort=1)
    objekt.kontakte.append(kontakt)
    session.flush()
    kontakt_id = kontakt.id

    _sync_kontakte(session, _satz(session, org, objekt), objekt, [], None)

    assert objekt.kontakte == []
    assert session.get(ObjektKontakt, kontakt_id) is None
