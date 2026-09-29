"""Regressionen fuer die transaktionsneutrale Objekt-Schreibschicht."""
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.kontakt import Kontakt
from app.models.master import FireDept
from app.models.objekt import GefahrenKatalog, MerkmalKatalog, Objekt, ObjektChange


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


@pytest.fixture()
def schreiben_env():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    set_tenant_context(session, None)
    org = FireDept(slug="schreiben", name="Schreiben", color="#000", bos="Feuerwehr")
    session.add(org)
    session.flush()
    objekt = Objekt(org_id=org.id, nummer=1, name="Objekt", status="entwurf")
    session.add(objekt)
    session.commit()
    yield session, org.id, objekt
    session.close()
    Base.metadata.drop_all(engine)


def test_objekt_anlegen_flush_rollback_und_quelle(schreiben_env):
    from app.services.objekt_pflege_schreiben_service import erstelle_objekt

    db, org_id, _ = schreiben_env
    objekt, geocodieren = erstelle_objekt(db, org_id=org_id, user_id=None, name="Neu", ort="Wien", quelle="mcp")
    assert objekt.id and geocodieren
    assert db.query(ObjektChange).filter_by(objekt_id=objekt.id).one().quelle == "mcp"
    db.rollback()
    assert db.query(Objekt).filter_by(name="Neu").first() is None


def test_objekt_anlegen_validierung(schreiben_env):
    from app.services.objekt_pflege_schreiben_service import ObjektValidierungsFehler, erstelle_objekt

    db, org_id, _ = schreiben_env
    with pytest.raises(ObjektValidierungsFehler, match="Name ist erforderlich"):
        erstelle_objekt(db, org_id=org_id, user_id=None, name=" ")


def test_objekt_anlegen_retry_bei_nummern_kollision(schreiben_env, monkeypatch):
    from app.services import objekt_pflege_schreiben_service as service

    db, org_id, _ = schreiben_env
    db.add(Objekt(org_id=org_id, nummer=2, name="Schon da", status="entwurf"))
    db.commit()
    nummern = iter((2, 3))
    monkeypatch.setattr(service, "naechste_nummer", lambda _db, _org_id: next(nummern))
    objekt, _ = service.erstelle_objekt(db, org_id=org_id, user_id=None, name="Nach Retry")
    assert objekt.nummer == 3


def test_bma_zusatzadresse_gefahr_merkmal_und_wohnanlage(schreiben_env):
    from app.services.objekt_pflege_schreiben_service import (
        bma_speichern,
        gefahr_anlegen,
        merkmal_zuordnen,
        wohnanlage_speichern,
        zusatzadresse_anlegen,
    )

    db, org_id, objekt = schreiben_env
    gefahr = GefahrenKatalog(org_id=org_id, name="Gas", aktiv=True)
    merkmal = MerkmalKatalog(org_id=org_id, name="Tor", aktiv=True)
    db.add_all([gefahr, merkmal])
    db.flush()
    bma_speichern(db, objekt, user_id=None, vorhanden=True, daten={"bma_nummer": "12"})
    zusatzadresse_anlegen(db, objekt, user_id=None, bezeichnung="Tor")
    gefahr_anlegen(db, objekt, user_id=None, gefahr_id=gefahr.id)
    assert merkmal_zuordnen(db, objekt, user_id=None, merkmal_id=merkmal.id) is not None
    assert merkmal_zuordnen(db, objekt, user_id=None, merkmal_id=merkmal.id) is None
    wohnanlage_speichern(db, objekt, user_id=None, vorhanden=True, daten={"wohneinheiten": 2})
    assert objekt.bma.bma_nummer == "12" and objekt.wohnanlage.wohneinheiten == 2


def test_kontakt_zuordnung_duplikat_und_create_ohne_commit(schreiben_env):
    from app.services.kontakt_service import create_kontakt
    from app.services.objekt_pflege_schreiben_service import ObjektValidierungsFehler, kontakt_zuordnen

    db, org_id, objekt = schreiben_env
    kontakt = create_kontakt(
        db, {"anzeigename": "Max", "typ": "person"}, [], [],
        org_id=org_id, user_id=None, commit=False,
    )
    kontakt_zuordnen(db, objekt, kontakt, art="sonstig", user_id=None)
    with pytest.raises(ObjektValidierungsFehler, match="bereits"):
        kontakt_zuordnen(db, objekt, kontakt, art="sonstig", user_id=None)
    db.rollback()
    assert db.query(Kontakt).filter_by(anzeigename="Max").first() is None
