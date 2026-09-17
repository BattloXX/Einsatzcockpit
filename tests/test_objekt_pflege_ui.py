"""Zusatzlogik der internen Objektpflege-Oberflaeche."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context  # noqa: E402
from app.db import Base  # noqa: E402
from app.models.kontakt import Kontakt  # noqa: E402
from app.models.master import FireDept  # noqa: E402
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    PFLEGEAUFTRAG_STATUS_EINGEREICHT,
    Objekt,
    ObjektKontakt,
)  # noqa: E402
from app.services.objekt_pflege_service import (  # noqa: E402
    ERMITTELT_AKTUALITAET_AKTUELL,
    ERMITTELT_AKTUALITAET_BALD_FAELLIG,
    ERMITTELT_AKTUALITAET_FREIGABE_ERFORDERLICH,
    ERMITTELT_AKTUALITAET_KEIN_KONTAKT,
    ERMITTELT_AKTUALITAET_PRUEFUNG_LAEUFT,
    ERMITTELT_AKTUALITAET_UEBERFAELLIG,
    bereiche_liste,
    ermittle_objekt_aktualitaet,
    erstelle_pflegeauftrag,
    hole_offenen_pflegeauftrag,
    hole_pflegeauftrag_by_token,
    rotiere_pflegeauftrag_token,
    verlaengere_pflegeauftrag,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def _daten(db):
    org = FireDept(slug="pflege-ui", name="Pflege UI", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    set_tenant_context(db, org.id)
    objekt = Objekt(org_id=org.id, nummer=1, name="Objekt", status=OBJEKT_STATUS_FREIGEGEBEN)
    kontakt = Kontakt(org_id=org.id, anzeigename="Kontakt", email="kontakt@example.test")
    db.add_all([objekt, kontakt])
    db.flush()
    db.add(ObjektKontakt(org_id=org.id, objekt_id=objekt.id, kontakt_id=kontakt.id, art="betreiber"))
    db.commit()
    return objekt, kontakt


def test_offener_auftrag_bereiche_token_rotation_und_verlaengerung(db):
    objekt, kontakt = _daten(db)
    auftrag, raw = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["adresse", "stammdaten"])
    db.flush()
    assert hole_offenen_pflegeauftrag(db, objekt) is auftrag
    assert bereiche_liste(auftrag) == ["adresse", "stammdaten"]
    auftrag.bereiche_json = "kaputt"
    assert bereiche_liste(auftrag) == []
    auftrag.bereiche_json = '["stammdaten", 4]'
    assert bereiche_liste(auftrag) == ["stammdaten"]

    old_hash = auftrag.token_hash
    new_raw = rotiere_pflegeauftrag_token(db, auftrag)
    assert auftrag.token_hash != old_hash
    assert hole_pflegeauftrag_by_token(db, raw) is None
    assert hole_pflegeauftrag_by_token(db, new_raw) is auftrag

    verlaengere_pflegeauftrag(db, auftrag, 30)
    assert auftrag.gueltig_bis >= datetime.now(UTC) + timedelta(days=29)
    assert auftrag.gueltig_bis <= datetime.now(UTC) + timedelta(days=31)


def test_ermittle_objekt_aktualitaet_prioritaeten(db):
    objekt, kontakt = _daten(db)
    assert (
        ermittle_objekt_aktualitaet(objekt, offener_auftrag=None, hat_email_kontakt=False)
        == ERMITTELT_AKTUALITAET_KEIN_KONTAKT
    )

    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    assert (
        ermittle_objekt_aktualitaet(objekt, offener_auftrag=auftrag, hat_email_kontakt=False)
        == ERMITTELT_AKTUALITAET_PRUEFUNG_LAEUFT
    )
    auftrag.status = PFLEGEAUFTRAG_STATUS_EINGEREICHT
    assert (
        ermittle_objekt_aktualitaet(objekt, offener_auftrag=auftrag, hat_email_kontakt=False)
        == ERMITTELT_AKTUALITAET_FREIGABE_ERFORDERLICH
    )

    objekt.revision_datum = date.today() - timedelta(days=1)
    assert (
        ermittle_objekt_aktualitaet(objekt, offener_auftrag=None, hat_email_kontakt=True)
        == ERMITTELT_AKTUALITAET_UEBERFAELLIG
    )
    objekt.revision_datum = date.today() + timedelta(days=30)
    assert (
        ermittle_objekt_aktualitaet(objekt, offener_auftrag=None, hat_email_kontakt=True)
        == ERMITTELT_AKTUALITAET_BALD_FAELLIG
    )
    objekt.revision_datum = date.today() + timedelta(days=31)
    assert (
        ermittle_objekt_aktualitaet(objekt, offener_auftrag=None, hat_email_kontakt=True)
        == ERMITTELT_AKTUALITAET_AKTUELL
    )
