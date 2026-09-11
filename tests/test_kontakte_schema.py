"""Schema-Grundlagen fuer das zentrale Kontakte-Modul."""
from sqlalchemy import BigInteger, create_engine, inspect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.kontakt import (
    Kontakt,
    KontaktAnhang,
    KontaktExterneReferenz,
    KontaktKategorie,
    KontaktKategorieZuordnung,
    KontaktTelefon,
    ObjektKontaktFreigabe,
)
from app.models.master import FireDept
from app.models.objekt import Objekt, ObjektKontakt


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


def test_kontakte_tabellen_und_tenant_scoping():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    assert {
        "kontakt", "kontakt_telefon", "kontakt_kategorie", "kontakt_kategorie_zuordnung",
        "kontakt_anhang", "kontakt_externe_referenz", "objekt_kontakt_freigabe",
    } <= set(inspect(engine).get_table_names())

    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)
    org_a = FireDept(slug="kontakte-a", name="Kontakte A", color="#f00", bos="Feuerwehr")
    org_b = FireDept(slug="kontakte-b", name="Kontakte B", color="#00f", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    kontakt_a = Kontakt(org_id=org_a.id, anzeigename="Kontakt A")
    kontakt_b = Kontakt(org_id=org_b.id, anzeigename="Kontakt B")
    objekt_a = Objekt(org_id=org_a.id, nummer=1, name="Objekt A", status="entwurf")
    db.add_all([kontakt_a, kontakt_b, objekt_a])
    db.flush()
    objekt_kontakt = ObjektKontakt(org_id=org_a.id, objekt_id=objekt_a.id, art="sonstig", name="Alt")
    db.add(objekt_kontakt)
    db.flush()

    db.add_all([
        KontaktTelefon(org_id=org_a.id, kontakt_id=kontakt_a.id, nummer="00 43 664 123456"),
        KontaktKategorie(org_id=org_a.id, name="Kategorie A"),
        KontaktAnhang(
            org_id=org_a.id, kontakt_id=kontakt_a.id, dateiname="a.pdf",
            medientyp="application/pdf", speicher_pfad="kontakte/a.pdf",
        ),
        KontaktExterneReferenz(org_id=org_a.id, kontakt_id=kontakt_a.id, quelle="test", extern_id="a"),
        ObjektKontaktFreigabe(
            org_id=org_a.id, objekt_kontakt_id=objekt_kontakt.id,
            kanal="sms", ziel_wert="+43664123456",
        ),
    ])
    db.flush()
    kategorie = db.query(KontaktKategorie).one()
    db.add(KontaktKategorieZuordnung(org_id=org_a.id, kontakt_id=kontakt_a.id, kategorie_id=kategorie.id))
    db.commit()

    set_tenant_context(db, org_a.id)
    assert db.query(Kontakt).count() == 1
    assert db.query(KontaktTelefon).one().nummer_normalisiert == "+43664123456"
    assert db.query(KontaktKategorieZuordnung).count() == 1
    assert db.query(KontaktAnhang).count() == 1
    assert db.query(KontaktExterneReferenz).count() == 1
    assert db.query(ObjektKontaktFreigabe).count() == 1

    set_tenant_context(db, org_b.id)
    assert db.query(Kontakt).all() == [kontakt_b]
    assert db.query(KontaktTelefon).count() == 0
    assert db.query(KontaktKategorie).count() == 0
    assert db.query(KontaktKategorieZuordnung).count() == 0
    assert db.query(KontaktAnhang).count() == 0
    assert db.query(KontaktExterneReferenz).count() == 0
    assert db.query(ObjektKontaktFreigabe).count() == 0

    set_tenant_context(db, org_a.id)
    assert db.query(ObjektKontakt).one().kontakt_id is None
    db.query(ObjektKontakt).one().kontakt_id = kontakt_a.id
    db.commit()
    assert db.query(ObjektKontakt).one().kontakt_id == kontakt_a.id
    db.close()
    Base.metadata.drop_all(bind=engine)
