"""Service-Tests fuer die interne Freigabe von Objekt-Pflegeaufträgen."""
import json

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.kontakt import Kontakt
from app.models.master import FireDept
from app.models.objekt import (
    OBJEKT_STATUS_ARCHIVIERT,
    OBJEKT_STATUS_FREIGEGEBEN,
    KontaktAenderungsvorschlag,
    Objekt,
    ObjektDokument,
    ObjektKontakt,
)
from app.services.kontakt_service import KontaktKonflikt
from app.services.objekt_pflege_service import (
    erstelle_pflegeauftrag,
    freigabe_transaktion,
    hole_oder_erstelle_arbeitskopie_fuer_auftrag,
    nacharbeit_anfordern,
    verwerfen_transaktion,
    wende_externe_feldaenderungen_an,
)
from app.services.objekt_service import aktualisiere_felder


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def _daten(db):
    org = FireDept(slug="review", name="Review", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    set_tenant_context(db, org.id)
    objekt = Objekt(org_id=org.id, nummer=1, name="Alt", status=OBJEKT_STATUS_FREIGEGEBEN)
    kontakt = Kontakt(org_id=org.id, anzeigename="Kontakt", email="alt@example.test", version=1)
    db.add_all([objekt, kontakt])
    db.flush()
    db.add(ObjektKontakt(org_id=org.id, objekt_id=objekt.id, kontakt_id=kontakt.id, art="betreiber"))
    db.commit()
    return org, objekt, kontakt


def test_freigabe_uebernimmt_kopie_kontakt_und_dokumente(db):
    org, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    kopie = hole_oder_erstelle_arbeitskopie_fuer_auftrag(db, auftrag)
    wende_externe_feldaenderungen_an(db, auftrag, kopie, {"name": "Neu"}, kontakt_id=kontakt.id)
    vorschlag = KontaktAenderungsvorschlag(
        org_id=org.id, pflegeauftrag_id=auftrag.id, kontakt_id=kontakt.id, basis_version=1,
        diff_json=json.dumps({"email": {"alt": "alt@example.test", "neu": "neu@example.test"}}),
    )
    alt = ObjektDokument(org_id=org.id, objekt_id=objekt.id, dateiname_original="alt.pdf", pfad="alt.pdf",
                         mime="application/pdf", dokument_gruppe_id=None, ist_aktuelle_version=True)
    db.add_all([vorschlag, alt])
    db.flush()
    neu = ObjektDokument(org_id=org.id, objekt_id=objekt.id, dateiname_original="neu.pdf", pfad="neu.pdf",
                         mime="application/pdf", dokument_gruppe_id=alt.id, versionsnummer=2,
                         ist_aktuelle_version=False, freigabe_status="wartet_freigabe", pflegeauftrag_id=auftrag.id)
    neu_dokument = ObjektDokument(org_id=org.id, objekt_id=objekt.id, dateiname_original="neu2.pdf", pfad="neu2.pdf",
                                  mime="application/pdf", ist_aktuelle_version=False,
                                  freigabe_status="wartet_freigabe", pflegeauftrag_id=auftrag.id)
    db.add_all([neu, neu_dokument])
    db.flush()
    auftrag.status = "eingereicht"

    produktiv = freigabe_transaktion(
        db, auftrag, user_id=7, kontakt_vorschlag_freigeben={vorschlag.id},
        kontakt_vorschlag_verwerfen=set(), dokument_freigeben={neu.id, neu_dokument.id},
        dokument_verwerfen=set(), dokument_archivieren=set(),
    )
    db.commit()

    assert produktiv.id == objekt.id
    assert db.get(Objekt, kopie.id) is None
    assert objekt.name == "Neu"
    assert kontakt.email == "neu@example.test"
    assert kontakt.version == 2
    assert alt.freigabe_status == "archiviert" and not alt.ist_aktuelle_version
    assert neu.freigabe_status == "freigegeben" and neu.ist_aktuelle_version
    assert neu_dokument.freigabe_status == "freigegeben" and neu_dokument.ist_aktuelle_version
    assert objekt.letzte_bestaetigung_am is not None
    assert objekt.letzte_bestaetigung_kontakt_id == kontakt.id
    assert objekt.letzte_bestaetigung_pflegeauftrag_id == auftrag.id
    assert auftrag.status == "freigegeben"


def test_doppelte_freigabe_wird_nach_statuswechsel_abgewiesen(db):
    _, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    auftrag.status = "eingereicht"

    freigabe_transaktion(
        db, auftrag, user_id=7, kontakt_vorschlag_freigeben=set(),
        kontakt_vorschlag_verwerfen=set(), dokument_freigeben=set(), dokument_verwerfen=set(),
        dokument_archivieren=set(),
    )
    db.commit()
    freigegeben_am = auftrag.freigegeben_am
    revision_datum = objekt.revision_datum

    with pytest.raises(ValueError, match="Nur eingereichte"):
        freigabe_transaktion(
            db, auftrag, user_id=8, kontakt_vorschlag_freigeben=set(),
            kontakt_vorschlag_verwerfen=set(), dokument_freigeben=set(), dokument_verwerfen=set(),
            dokument_archivieren=set(),
        )

    assert auftrag.status == "freigegeben"
    assert auftrag.freigegeben_am == freigegeben_am
    assert objekt.revision_datum == revision_datum


def test_freigabe_eines_zwischenzeitlich_archivierten_objekts_mutiert_nichts(db):
    _, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    auftrag.status = "eingereicht"
    objekt.status = OBJEKT_STATUS_ARCHIVIERT
    db.commit()

    with pytest.raises(ValueError, match="zwischenzeitlich archiviert"):
        freigabe_transaktion(
            db, auftrag, user_id=7, kontakt_vorschlag_freigeben=set(),
            kontakt_vorschlag_verwerfen=set(), dokument_freigeben=set(), dokument_verwerfen=set(),
            dokument_archivieren=set(),
        )

    assert auftrag.status == "eingereicht"
    assert objekt.letzte_bestaetigung_am is None
    assert objekt.revision_datum is None


def test_unvollstaendige_freigabe_mutiert_nichts(db):
    _, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["kontakte"])
    vorschlag = KontaktAenderungsvorschlag(
        org_id=objekt.org_id, pflegeauftrag_id=auftrag.id, kontakt_id=kontakt.id, basis_version=1,
        diff_json=json.dumps({"email": {"alt": "alt@example.test", "neu": "neu@example.test"}}),
    )
    db.add(vorschlag)
    auftrag.status = "eingereicht"
    with pytest.raises(ValueError, match="Kontaktvorschlaege"):
        freigabe_transaktion(
            db, auftrag, user_id=1, kontakt_vorschlag_freigeben=set(),
            kontakt_vorschlag_verwerfen=set(), dokument_freigeben=set(), dokument_verwerfen=set(),
            dokument_archivieren=set(),
        )
    assert vorschlag.status == "offen"
    assert kontakt.email == "alt@example.test"


def test_nacharbeit_rotiert_token(db):
    _, objekt, kontakt = _daten(db)
    auftrag, raw = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten"])
    auftrag.status = "eingereicht"
    neu = nacharbeit_anfordern(db, auftrag, user_id=1, text="Bitte ergänzen")
    assert raw != neu
    assert auftrag.status == "nacharbeit"
    assert auftrag.nacharbeit_text == "Bitte ergänzen"


def test_freigabe_propagiert_kontaktkonflikt_und_mutiert_nichts(db):
    org, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["kontakte"])
    vorschlag = KontaktAenderungsvorschlag(
        org_id=org.id, pflegeauftrag_id=auftrag.id, kontakt_id=kontakt.id, basis_version=1,
        diff_json=json.dumps({"email": {"alt": "alt@example.test", "neu": "neu@example.test"}}),
    )
    db.add(vorschlag)
    db.flush()
    auftrag.status = "eingereicht"
    # Kontakt wurde zwischenzeitlich anderweitig geaendert (version laeuft dem
    # basis_version des Vorschlags davon) - simuliert einen echten Konflikt.
    kontakt.version = 2
    db.commit()

    with pytest.raises(KontaktKonflikt):
        freigabe_transaktion(
            db, auftrag, user_id=1, kontakt_vorschlag_freigeben={vorschlag.id},
            kontakt_vorschlag_verwerfen=set(), dokument_freigeben=set(), dokument_verwerfen=set(),
            dokument_archivieren=set(),
        )
    db.rollback()
    assert db.get(KontaktAenderungsvorschlag, vorschlag.id).status == "offen"
    assert db.get(Kontakt, kontakt.id).email == "alt@example.test"
    assert db.get(Objekt, objekt.id).letzte_bestaetigung_am is None


def test_verwerfen_transaktion_erhaelt_interne_aenderung_und_verwirft_vorschlaege(db):
    org, objekt, kontakt = _daten(db)
    auftrag, _ = erstelle_pflegeauftrag(db, objekt, kontakt, ersteller_id=None, bereiche=["stammdaten", "kontakte"])
    kopie = hole_oder_erstelle_arbeitskopie_fuer_auftrag(db, auftrag)
    wende_externe_feldaenderungen_an(db, auftrag, kopie, {"name": "Extern"}, kontakt_id=kontakt.id)
    aktualisiere_felder(db, kopie, {"vulgoname": "Intern"}, bereich="stammdaten", user_id=1)
    vorschlag = KontaktAenderungsvorschlag(
        org_id=org.id, pflegeauftrag_id=auftrag.id, kontakt_id=kontakt.id, basis_version=1,
        diff_json=json.dumps({"email": {"alt": "alt@example.test", "neu": "neu@example.test"}}),
    )
    dokument = ObjektDokument(org_id=org.id, objekt_id=objekt.id, dateiname_original="neu.pdf", pfad="neu.pdf",
                              mime="application/pdf", ist_aktuelle_version=False,
                              freigabe_status="wartet_freigabe", pflegeauftrag_id=auftrag.id)
    db.add_all([vorschlag, dokument])
    db.flush()
    auftrag.status = "eingereicht"

    verwerfen_transaktion(db, auftrag, user_id=1)
    db.commit()

    assert db.get(Objekt, kopie.id) is kopie
    assert kopie.name == "Alt"
    assert kopie.vulgoname == "Intern"
    assert vorschlag.status == "verworfen"
    assert dokument.freigabe_status == "verworfen"
    assert kontakt.email == "alt@example.test"
    assert auftrag.status == "verworfen"
