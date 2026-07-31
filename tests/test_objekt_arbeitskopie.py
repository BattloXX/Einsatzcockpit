"""Objektverwaltung: Arbeitskopie-Workflow (Entwurf -> Freigabe -> Ueberarbeitung).

Deckt das Kernstueck des Arbeitskopie-Konzepts ab (docs/plans/objekt-arbeitskopie-plan.md):
erstelle_arbeitskopie/uebernimm_arbeitskopie/verwirf_arbeitskopie in objekt_service.py,
sowie die Leak-Vermeidung (nur_produktiv) an den kritischen Abfragestellen.
"""
from datetime import date

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.exc import IntegrityError
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
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_STATUS_UEBERARBEITUNG,
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektChange,
    ObjektEinsatz,
    ObjektGefahr,
    ObjektKartenObjekt,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektWohnanlage,
    ObjektZusatzadresse,
)
from app.services.objekt_service import (
    build_sync_manifest,
    erstelle_arbeitskopie,
    hole_arbeitskopie,
    nur_produktiv,
    pruefe_revision_erinnerungen,
    uebernimm_arbeitskopie,
    verwirf_arbeitskopie,
)


@pytest.fixture()
def db():
    """Frisches In-Memory-Schema je Test - der Arbeitskopie-Workflow mutiert stark
    (Merge/Verwerfen loescht Zeilen), ein modul-weit geteilter State waere fehleranfaellig."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def org(db):
    o = FireDept(slug="ak-org", name="Org Arbeitskopie", color="#ff0000", bos="Feuerwehr")
    db.add(o)
    db.flush()
    set_tenant_context(db, o.id)
    return o


def _volles_objekt(db, org_id: int) -> Objekt:
    """Baut ein freigegebenes Objekt mit allen kopierbaren Kinddaten auf (BMA,
    Zusatzadresse, Gefahr, Merkmal, 2 Kontakte, Wohnanlage mit Hausverwaltung, 1
    Karten-Symbol) - so laesst sich die Tiefkopie an jeder Kind-Tabelle pruefen."""
    gefahr_katalog = GefahrenKatalog(org_id=org_id, name="Gas", piktogramm_typ="gas")
    merkmal_katalog = MerkmalKatalog(org_id=org_id, code="schluesselbox", name="Schlüsselbox")
    db.add_all([gefahr_katalog, merkmal_katalog])
    db.flush()

    objekt = Objekt(
        org_id=org_id, nummer=1, name="Werk 2", strasse="Dammstraße", hausnummer="10",
        ort="Wolfurt", status=OBJEKT_STATUS_FREIGEGEBEN, revision_datum=date(2026, 1, 1),
    )
    db.add(objekt)
    db.flush()

    objekt.bma = ObjektBMA(org_id=org_id, objekt_id=objekt.id, bma_nummer="1044")
    db.add(ObjektZusatzadresse(org_id=org_id, objekt_id=objekt.id, bezeichnung="Stiege 2"))
    db.add(ObjektGefahr(org_id=org_id, objekt_id=objekt.id, gefahr_id=gefahr_katalog.id, detail="Lager"))
    db.add(ObjektMerkmal(org_id=org_id, objekt_id=objekt.id, merkmal_id=merkmal_katalog.id))
    kontakt1 = ObjektKontakt(org_id=org_id, objekt_id=objekt.id, art="betreiber", name="Betreiber AG")
    kontakt2 = ObjektKontakt(org_id=org_id, objekt_id=objekt.id, art="hausverwaltung", name="Hausverwaltung GmbH")
    db.add_all([kontakt1, kontakt2])
    db.add(ObjektKartenObjekt(org_id=org_id, objekt_id=objekt.id, typ="fsd", lat=47.4, lng=9.75))
    db.flush()
    objekt.wohnanlage = ObjektWohnanlage(
        org_id=org_id, objekt_id=objekt.id, wohneinheiten=12,
        hausverwaltung_kontakt_id=kontakt2.id,
    )
    db.commit()
    db.refresh(objekt)
    return objekt


# ── erstelle_arbeitskopie ──────────────────────────────────────────────────────

def test_erstelle_arbeitskopie_kopiert_alle_kinddaten(db, org):
    basis = _volles_objekt(db, org.id)
    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()
    db.refresh(basis)
    db.refresh(kopie)

    assert kopie.entwurf_von_id == basis.id
    assert kopie.status == OBJEKT_STATUS_ENTWURF
    assert kopie.nummer is None  # kollidiert sonst mit uq_objekt_org_nummer
    assert kopie.name == basis.name == "Werk 2"

    assert kopie.bma is not None and kopie.bma.id != basis.bma.id
    assert kopie.bma.bma_nummer == "1044"
    assert len(kopie.zusatzadressen) == 1
    assert len(kopie.gefahren) == 1 and kopie.gefahren[0].id != basis.gefahren[0].id
    assert len(kopie.merkmale) == 1
    assert len(kopie.kontakte) == 2
    assert {k.id for k in kopie.kontakte}.isdisjoint({k.id for k in basis.kontakte})
    assert len(kopie.karten_objekte) == 1

    # Wohnanlage.hausverwaltung_kontakt_id muss auf den KOPIERTEN Kontakt remappt sein,
    # nicht auf den Kontakt des Basis-Objekts.
    kopie_hv_kontakt = next(k for k in kopie.kontakte if k.art == "hausverwaltung")
    assert kopie.wohnanlage.hausverwaltung_kontakt_id == kopie_hv_kontakt.id
    assert kopie.wohnanlage.hausverwaltung_kontakt_id not in {k.id for k in basis.kontakte}

    # Basis bleibt inhaltlich unangetastet, wechselt nur den Status.
    assert basis.status == OBJEKT_STATUS_UEBERARBEITUNG
    assert basis.name == "Werk 2"
    assert basis.bma.bma_nummer == "1044"
    assert len(basis.kontakte) == 2


def test_erstelle_arbeitskopie_nur_bei_freigegeben(db, org):
    objekt = Objekt(org_id=org.id, nummer=1, name="Entwurf-Objekt", status=OBJEKT_STATUS_ENTWURF)
    db.add(objekt)
    db.commit()
    with pytest.raises(ValueError):
        erstelle_arbeitskopie(db, objekt, user_id=None)


def test_zweite_arbeitskopie_wird_service_seitig_blockiert(db, org):
    basis = _volles_objekt(db, org.id)
    erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()
    db.refresh(basis)
    with pytest.raises(ValueError):
        erstelle_arbeitskopie(db, basis, user_id=None)


def test_zweite_arbeitskopie_wird_db_seitig_blockiert(db, org):
    """Defense-in-depth: der UNIQUE-Index auf entwurf_von_id greift auch, wenn der
    Service-Check umgangen wuerde (z. B. direktes Insert)."""
    basis = _volles_objekt(db, org.id)
    db.add(Objekt(org_id=org.id, nummer=None, name="Kopie 1",
                  status=OBJEKT_STATUS_ENTWURF, entwurf_von_id=basis.id))
    db.commit()
    db.add(Objekt(org_id=org.id, nummer=None, name="Kopie 2",
                  status=OBJEKT_STATUS_ENTWURF, entwurf_von_id=basis.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ── uebernimm_arbeitskopie (Merge) ─────────────────────────────────────────────

def test_uebernimm_arbeitskopie_schreibt_auf_stabile_basis_id(db, org):
    basis = _volles_objekt(db, org.id)
    basis_id = basis.id
    # Verknuepfung, die per FK an der Basis-id haengt - darf den Merge nicht spueren.
    db.add(ObjektEinsatz(org_id=org.id, objekt_id=basis_id, incident_id=1, quelle="manuell", status="bestaetigt"))
    db.commit()

    kopie = erstelle_arbeitskopie(db, basis, user_id=7)
    db.commit()
    kopie_id = kopie.id

    kopie.name = "Werk 2 (saniert)"
    kopie.bma.bma_nummer = "2099"
    db.add(ObjektKontakt(org_id=org.id, objekt_id=kopie.id, art="sonstig", name="Neuer Kontakt"))
    db.commit()
    db.refresh(kopie)

    aktualisiert = uebernimm_arbeitskopie(db, kopie, user_id=7)
    db.commit()

    assert aktualisiert.id == basis_id  # id bleibt stabil - ObjektEinsatz braucht keine Anpassung
    assert aktualisiert.status == OBJEKT_STATUS_FREIGEGEBEN
    assert aktualisiert.name == "Werk 2 (saniert)"
    assert aktualisiert.bma.bma_nummer == "2099"
    assert len(aktualisiert.kontakte) == 3

    # Kopie ist weg.
    assert db.query(Objekt).filter(Objekt.id == kopie_id).first() is None

    # Die Einsatz-Verknuepfung ist unveraendert erhalten (gleiche Basis-id).
    verknuepfung = db.query(ObjektEinsatz).filter(ObjektEinsatz.objekt_id == basis_id).first()
    assert verknuepfung is not None

    # Protokoll der Kopie wurde auf die Basis umgehaengt (Historie bleibt erhalten).
    changes = db.query(ObjektChange).filter(ObjektChange.objekt_id == basis_id).all()
    assert any(c.feld == "name" for c in changes)


def test_uebernimm_arbeitskopie_remappt_wohnanlage_kontakt(db, org):
    basis = _volles_objekt(db, org.id)
    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()
    db.refresh(kopie)

    aktualisiert = uebernimm_arbeitskopie(db, kopie, user_id=None)
    db.commit()
    db.refresh(aktualisiert)

    assert aktualisiert.wohnanlage is not None
    hv_kontakt = next(k for k in aktualisiert.kontakte if k.art == "hausverwaltung")
    assert aktualisiert.wohnanlage.hausverwaltung_kontakt_id == hv_kontakt.id


def test_uebernimm_arbeitskopie_mit_importkontakten_verletzt_unique_nicht(db, org):
    basis = _volles_objekt(db, org.id)
    basis_hv = next(k for k in basis.kontakte if k.art == "hausverwaltung")
    basis_hv.extern_quelle = "dibos_bma"
    basis_hv.extern_id = "pdf:1332:hausverwaltung:hausverwaltung-gmbh"
    db.commit()

    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()
    assert next(k for k in kopie.kontakte if k.art == "hausverwaltung").extern_id == basis_hv.extern_id

    aktualisiert = uebernimm_arbeitskopie(db, kopie, user_id=None)
    db.commit()
    db.refresh(aktualisiert)

    treffer = db.query(ObjektKontakt).filter(
        ObjektKontakt.objekt_id == aktualisiert.id,
        ObjektKontakt.extern_id == "pdf:1332:hausverwaltung:hausverwaltung-gmbh",
    ).all()
    assert len(treffer) == 1
    assert aktualisiert.wohnanlage.hausverwaltung_kontakt_id in {k.id for k in aktualisiert.kontakte}


def test_uebernimm_arbeitskopie_erhaelt_merkmale_gefahren_zusatzadressen_kartenobjekte(db, org):
    """Vorfall 2026-07-26 (Objekt 1082, Testserver): _ersetze_kinddaten() loeschte die
    alten Zeilen von Zusatzadresse/Gefahr/Merkmal/Kartenobjekt, ohne vor dem Anlegen
    der Kopien zu flushen (anders als beim BMA-/Wohnanlage-/Kontakte-Block, die das
    bereits richtig machten) - auf MySQL (batched INSERT) lief der INSERT der Kopie
    dem DELETE der alten Zeile davon und schlug mit "Duplicate entry ... for key
    'uq_objekt_merkmal'" fehl (auf SQLite zufaellig nicht reproduzierbar, da die
    Flush-Reihenfolge dort anders ausfaellt - deshalb hier eine reine Ergebnis-
    Pruefung statt eines Versuchs, die Datenbank-spezifische Race exakt nachzustellen).
    Bisher pruefte keine dieser Tests ueberhaupt, ob diese vier Kind-Tabellen die
    Uebernahme ueberleben - nur bma/kontakte/wohnanlage waren abgedeckt."""
    basis = _volles_objekt(db, org.id)
    basis_id = basis.id
    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()

    aktualisiert = uebernimm_arbeitskopie(db, kopie, user_id=None)
    db.commit()
    db.refresh(aktualisiert)

    assert aktualisiert.id == basis_id
    assert len(aktualisiert.zusatzadressen) == 1
    assert aktualisiert.zusatzadressen[0].bezeichnung == "Stiege 2"
    assert len(aktualisiert.gefahren) == 1
    assert aktualisiert.gefahren[0].detail == "Lager"
    assert len(aktualisiert.merkmale) == 1
    assert len(aktualisiert.karten_objekte) == 1

    # Genau je eine Zeile in der DB - keine Dubletten, keine Karteileichen der Kopie.
    assert db.query(ObjektZusatzadresse).filter(ObjektZusatzadresse.objekt_id == basis_id).count() == 1
    assert db.query(ObjektGefahr).filter(ObjektGefahr.objekt_id == basis_id).count() == 1
    assert db.query(ObjektMerkmal).filter(ObjektMerkmal.objekt_id == basis_id).count() == 1
    assert db.query(ObjektKartenObjekt).filter(ObjektKartenObjekt.objekt_id == basis_id).count() == 1


def test_uebernimm_ohne_offene_ueberarbeitung_lehnt_ab(db, org):
    basis = _volles_objekt(db, org.id)
    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()
    basis.status = OBJEKT_STATUS_FREIGEGEBEN  # inkonsistent - simuliert einen Race
    db.commit()
    with pytest.raises(ValueError):
        uebernimm_arbeitskopie(db, kopie, user_id=None)


# ── verwirf_arbeitskopie ────────────────────────────────────────────────────────

def test_verwirf_arbeitskopie_laesst_basis_unveraendert(db, org):
    basis = _volles_objekt(db, org.id)
    original_name = basis.name
    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()
    kopie_id = kopie.id

    kopie.name = "Sollte verworfen werden"
    db.commit()

    ergebnis = verwirf_arbeitskopie(db, kopie, user_id=None)
    db.commit()
    db.refresh(ergebnis)

    assert ergebnis.id == basis.id
    assert ergebnis.name == original_name
    assert ergebnis.status == OBJEKT_STATUS_FREIGEGEBEN
    assert db.query(Objekt).filter(Objekt.id == kopie_id).first() is None


# ── nur_produktiv / hole_arbeitskopie ───────────────────────────────────────────

def test_nur_produktiv_blendet_arbeitskopie_aus(db, org):
    basis = _volles_objekt(db, org.id)
    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()

    alle = db.query(Objekt).all()
    produktiv = nur_produktiv(db.query(Objekt)).all()
    assert kopie.id in {o.id for o in alle}
    assert kopie.id not in {o.id for o in produktiv}
    assert basis.id in {o.id for o in produktiv}


def test_hole_arbeitskopie(db, org):
    basis = _volles_objekt(db, org.id)
    assert hole_arbeitskopie(db, basis) is None
    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()
    gefunden = hole_arbeitskopie(db, basis)
    assert gefunden is not None and gefunden.id == kopie.id


# ── Leak-Vermeidung an konkreten Konsumenten ───────────────────────────────────

def test_pruefe_revision_erinnerungen_ignoriert_arbeitskopie(db, org):
    """Die Kopie erbt beim Kopieren das revision_datum der Basis - ohne nur_produktiv
    wuerde das eine zweite (Karteileichen-)Erinnerung fuer dasselbe Objekt ausloesen."""
    basis = _volles_objekt(db, org.id)
    basis.revision_datum = date(2020, 1, 1)  # laengst faellig
    db.commit()
    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    db.commit()
    assert kopie.revision_datum == basis.revision_datum

    faellig = pruefe_revision_erinnerungen(db)
    db.commit()
    assert [f["objekt_id"] for f in faellig] == [basis.id]


def test_build_sync_manifest_ignoriert_arbeitskopie_auch_bei_falschem_status(db, org):
    """Defense-in-depth: selbst wenn eine Kopie (durch einen Bug) auf 'freigegeben'
    stuende, darf sie nie im Offline-Sync-Manifest landen - Dokumente/Medien fehlen ihr.
    erstelle_arbeitskopie() setzt die Basis regulaer auf 'in_ueberarbeitung' (raus aus dem
    Sync-Manifest, das nur 'freigegeben' liefert) - hier wird beides zurueckgedreht, um
    gezielt den Kopie-Leak zu pruefen, unabhaengig vom Basis-Status."""
    basis = _volles_objekt(db, org.id)
    kopie = erstelle_arbeitskopie(db, basis, user_id=None)
    basis.status = OBJEKT_STATUS_FREIGEGEBEN
    kopie.status = OBJEKT_STATUS_FREIGEGEBEN
    db.commit()

    manifest = build_sync_manifest(db, org.id)
    ids = {e["objekt_id"] for e in manifest["objekte"]}
    assert basis.id in ids
    assert kopie.id not in ids
