"""Objektverwaltung PR 2: Kataloge, Gefahren, Merkmale, Kontakte, Wohnanlage, Erinnerung."""
from datetime import date, timedelta

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
    OBJEKT_STATUS_FREIGEGEBEN,
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektGefahr,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektWohnanlage,
)
from app.services.objekt_service import (
    berechne_vollstaendigkeit,
    pruefe_revision_erinnerungen,
    seed_objekt_kataloge,
)


# ── Modelle / Registrierung ───────────────────────────────────────────────────

def test_pr2_models_in_tenant_tables():
    from app.core.tenant import _TENANT_TABLE_NAMES
    for tbl in ("gefahren_katalog", "objekt_gefahr", "merkmal_katalog",
                "objekt_merkmal", "objekt_kontakt", "objekt_wohnanlage"):
        assert tbl in _TENANT_TABLE_NAMES, f"{tbl} fehlt in _TENANT_TABLE_NAMES"


def test_pr2_models_importable_from_package():
    from app.models import (
        GefahrenKatalog,  # noqa: F401
        MerkmalKatalog,  # noqa: F401
        ObjektGefahr,  # noqa: F401
        ObjektKontakt,  # noqa: F401
        ObjektMerkmal,  # noqa: F401
        ObjektWohnanlage,  # noqa: F401
    )


def test_kontakt_telefone_roundtrip():
    k = ObjektKontakt(objekt_id=1, art="betreiber", name="Test",
                      telefone_json='["+43 5574 123", "+43 664 456"]')
    assert k.telefone == ["+43 5574 123", "+43 664 456"]
    k2 = ObjektKontakt(objekt_id=1, art="betreiber", name="Leer", telefone_json=None)
    assert k2.telefone == []
    k3 = ObjektKontakt(objekt_id=1, art="betreiber", name="Kaputt", telefone_json="{ungueltig")
    assert k3.telefone == []


def test_telefone_to_json_parser():
    from app.routers.ui_objekt import _telefone_to_json
    assert _telefone_to_json("+43 5574 123, +43 664 456") == '["+43 5574 123", "+43 664 456"]'
    assert _telefone_to_json("+43 5574 123; +43 664 456") == '["+43 5574 123", "+43 664 456"]'
    assert _telefone_to_json("   ") is None
    assert _telefone_to_json("") is None


# ── In-Memory-DB ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pr2_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    org_a = FireDept(slug="pr2-org-a", name="Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="pr2-org-b", name="Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    set_tenant_context(db, None)
    seed_objekt_kataloge(db, org_a.id)

    objekt = Objekt(org_id=org_a.id, nummer=1, name="Rattpack Werk 2",
                    status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt)
    db.commit()

    yield db, org_a.id, org_b.id, objekt.id

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_seed_kataloge(pr2_db):
    db, org_a_id, org_b_id, _ = pr2_db
    set_tenant_context(db, org_a_id)
    assert db.query(GefahrenKatalog).count() == 7
    assert db.query(MerkmalKatalog).count() == 11
    codes = {m.code for m in db.query(MerkmalKatalog).all()}
    assert {"schluesselbox", "brandschutzplan", "dlk_stellplatz"} <= codes
    # Idempotent: erneuter Seed erzeugt keine Duplikate
    set_tenant_context(db, None)
    seed_objekt_kataloge(db, org_a_id)
    db.commit()
    set_tenant_context(db, org_a_id)
    assert db.query(GefahrenKatalog).count() == 7
    assert db.query(MerkmalKatalog).count() == 11


def test_seed_kataloge_isolation(pr2_db):
    db, org_a_id, org_b_id, _ = pr2_db
    set_tenant_context(db, org_b_id)
    assert db.query(GefahrenKatalog).count() == 0
    assert db.query(MerkmalKatalog).count() == 0


def test_gefahr_zuordnung_und_unique_merkmal(pr2_db):
    db, org_a_id, _, objekt_id = pr2_db
    set_tenant_context(db, org_a_id)
    gefahr = db.query(GefahrenKatalog).filter(GefahrenKatalog.piktogramm_typ == "ex").first()
    merkmal = db.query(MerkmalKatalog).filter(MerkmalKatalog.code == "schluesselbox").first()

    db.add(ObjektGefahr(org_id=org_a_id, objekt_id=objekt_id, gefahr_id=gefahr.id,
                        un_nummer="1173", detail="Ethylacetat", sort=1))
    db.add(ObjektMerkmal(org_id=org_a_id, objekt_id=objekt_id, merkmal_id=merkmal.id,
                         hinweis="beim Haupteingang"))
    db.commit()

    objekt = db.get(Objekt, objekt_id)
    assert len(objekt.gefahren) == 1
    assert objekt.gefahren[0].un_nummer == "1173"
    assert objekt.hat_merkmal("schluesselbox") is True
    assert objekt.hat_merkmal("brandschutzplan") is False

    # Unique (objekt_id, merkmal_id)
    from sqlalchemy.exc import IntegrityError
    db.add(ObjektMerkmal(org_id=org_a_id, objekt_id=objekt_id, merkmal_id=merkmal.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_vollstaendigkeit_mit_kontakten_und_gefahren(pr2_db):
    db, org_a_id, _, objekt_id = pr2_db
    set_tenant_context(db, org_a_id)
    objekt = db.get(Objekt, objekt_id)
    v = berechne_vollstaendigkeit(objekt, kontakt_count=0, gefahren_count=1)
    assert "Kontakte" in v["fehlend"]
    assert "Gefahren" in v["erfuellt"]
    # Ohne counts bleiben die Punkte draussen
    v2 = berechne_vollstaendigkeit(objekt)
    assert "Kontakte" not in v2["fehlend"] and "Kontakte" not in v2["erfuellt"]


def test_wohnanlage_block(pr2_db):
    db, org_a_id, _, objekt_id = pr2_db
    set_tenant_context(db, org_a_id)
    kontakt = ObjektKontakt(org_id=org_a_id, objekt_id=objekt_id, art="hausverwaltung",
                            name="HV Muster", telefone_json='["+43 5574 999"]')
    db.add(kontakt)
    db.flush()
    db.add(ObjektWohnanlage(org_id=org_a_id, objekt_id=objekt_id, wohneinheiten=24,
                            geschosse=5, stiegen=3, hausverwaltung_kontakt_id=kontakt.id))
    db.commit()
    objekt = db.get(Objekt, objekt_id)
    assert objekt.wohnanlage is not None
    assert objekt.wohnanlage.wohneinheiten == 24
    assert objekt.wohnanlage.hausverwaltung_kontakt.name == "HV Muster"


# ── Revisions-Erinnerung ──────────────────────────────────────────────────────

def test_revision_erinnerung_und_kein_doppelversand(pr2_db):
    db, org_a_id, _, _ = pr2_db
    set_tenant_context(db, None)
    gestern = date.today() - timedelta(days=1)
    faelliges = Objekt(org_id=org_a_id, nummer=90, name="Revision faellig",
                       status=OBJEKT_STATUS_FREIGEGEBEN, revision_datum=gestern)
    zukunft = Objekt(org_id=org_a_id, nummer=91, name="Revision Zukunft",
                     status=OBJEKT_STATUS_FREIGEGEBEN,
                     revision_datum=date.today() + timedelta(days=30))
    archiviert = Objekt(org_id=org_a_id, nummer=92, name="Archiviert faellig",
                        status=OBJEKT_STATUS_ARCHIVIERT, revision_datum=gestern)
    db.add_all([faelliges, zukunft, archiviert])
    db.commit()

    treffer = pruefe_revision_erinnerungen(db)
    db.commit()
    namen = {t["name"] for t in treffer}
    assert "Revision faellig" in namen
    assert "Revision Zukunft" not in namen
    assert "Archiviert faellig" not in namen
    assert faelliges.revision_erinnert_am == date.today()

    # Zweiter Lauf: kein Doppelversand
    treffer2 = pruefe_revision_erinnerungen(db)
    db.commit()
    assert all(t["name"] != "Revision faellig" for t in treffer2)


def test_revision_erneut_nach_neuem_datum(pr2_db):
    db, org_a_id, _, _ = pr2_db
    set_tenant_context(db, None)
    objekt = db.query(Objekt).filter(Objekt.nummer == 90).execution_options(
        include_all_tenants=True).first()
    # Sachbearbeiter setzt neues Revisionsdatum → Router setzt den Marker zurueck
    # (siehe stammdaten_speichern) → bei erneuter Faelligkeit wieder erinnern
    objekt.revision_datum = date.today()
    objekt.revision_erinnert_am = None
    db.commit()
    treffer = pruefe_revision_erinnerungen(db)
    db.commit()
    assert any(t["name"] == "Revision faellig" for t in treffer)


# ── Katalog-Routen importierbar ───────────────────────────────────────────────

def test_pr2_routen_vorhanden():
    from app.routers.ui_objekt import router
    # Pfad-Konverter (":int" bei den Routen mit /kataloge-Geschwistern, um Shadowing
    # zu verhindern) für den Vergleich normalisieren – der Test prüft die Existenz der
    # Routen, nicht ihren Converter.
    pfade = {r.path.replace(":int", "") for r in router.routes}
    assert "/objekte/{objekt_id}/gefahren" in pfade
    assert "/objekte/{objekt_id}/merkmale" in pfade
    assert "/objekte/{objekt_id}/kontakte" in pfade
    assert "/objekte/{objekt_id}/wohnanlage" in pfade
    assert "/objekte/kataloge/gefahren/neu" in pfade
    assert "/objekte/kataloge/merkmale/neu" in pfade
