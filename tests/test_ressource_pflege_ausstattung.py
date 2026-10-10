"""Service-Tests für die lagebezogene Ausstattungspflege."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.atemschutz_pruefung import AtemschutzGeraet
from app.models.major_incident import LageEinheit, LageEinheitAusstattung, LageJournalEntry, MajorIncident
from app.models.master import FireDept, VehicleMaster
from app.services import ressource_pflege_service as service
from tests.conftest import TestingSession


@contextmanager
def _session():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _daten(db):
    suffix = uuid4().hex[:8]
    org = FireDept(slug=f"aus-{suffix}", name="Ausstattung", color="#123456", bos="Feuerwehr")
    fremd = FireDept(slug=f"aus-fremd-{suffix}", name="Fremd", color="#654321", bos="Feuerwehr")
    db.add_all([org, fremd])
    db.flush()
    fahrzeug = VehicleMaster(dept_id=org.id, code="RLF", name="RLF", type="RLF")
    lage = MajorIncident(name="Aktuell", org_id=org.id)
    andere_lage = MajorIncident(name="Andere", org_id=org.id)
    db.add_all([fahrzeug, lage, andere_lage])
    db.flush()
    a = LageEinheit(lage_id=lage.id, vehicle_id=fahrzeug.id, label="RLF A")
    b = LageEinheit(lage_id=lage.id, label="TLF B")
    c = LageEinheit(lage_id=andere_lage.id, label="Andere")
    db.add_all([a, b, c])
    db.flush()
    return org, fremd, lage, andere_lage, a, b, c, fahrzeug


def test_hinzufuegen_katalog_freitext_validierung_und_stammdatenreferenz():
    with _session() as db:
        org, fremd, lage, _, a, _, _, _ = _daten(db)
        geraet = AtemschutzGeraet(org_id=org.id, nummer="PA-1", bezeichnung="Pressluftatmer")
        fremdes_geraet = AtemschutzGeraet(org_id=fremd.id, nummer="PA-X")
        db.add_all([geraet, fremdes_geraet])
        db.flush()
        zeile = service.ausstattung_hinzufuegen(
            db,
            lage,
            a,
            kategorie="tauchpumpe",
            menge=2,
            stamm_ref_typ="atemschutz_geraet",
            stamm_ref_id=geraet.id,
            user_id=1,
            author_name="EL",
        )
        assert (zeile.bezeichnung, zeile.menge, geraet.nummer) == ("Tauchpumpe", 2, "PA-1")
        with pytest.raises(ValueError, match="Bezeichnung"):
            service.ausstattung_hinzufuegen(db, lage, a, kategorie="sonstiges", user_id=1, author_name="EL")
        with pytest.raises(ValueError):
            service.ausstattung_hinzufuegen(db, lage, a, kategorie="unbekannt", user_id=1, author_name="EL")
        with pytest.raises(ValueError):
            service.ausstattung_hinzufuegen(
                db, lage, a, kategorie="tauchpumpe", menge=10000, user_id=1, author_name="EL"
            )
        with pytest.raises(ValueError):
            service.ausstattung_hinzufuegen(
                db, lage, a, kategorie="tauchpumpe", status="kaputt", user_id=1, author_name="EL"
            )
        with pytest.raises(ValueError, match="Organisation"):
            service.ausstattung_hinzufuegen(
                db,
                lage,
                a,
                kategorie="atemschutzgeraete",
                stamm_ref_typ="atemschutz_geraet",
                stamm_ref_id=fremdes_geraet.id,
                user_id=1,
                author_name="EL",
            )
        assert geraet.bezeichnung == "Pressluftatmer"


def test_aendern_entfernen_fremde_zeile_warnungen_und_journal():
    with _session() as db:
        _, _, lage, _, a, b, _, _ = _daten(db)
        zeile = service.ausstattung_hinzufuegen(
            db, lage, a, kategorie="tauchpumpe", menge=2, user_id=1, author_name="EL"
        )
        service.ausstattung_aendern(db, lage, a, zeile.id, menge=2, user_id=1, author_name="EL")
        service.ausstattung_aendern(db, lage, a, zeile.id, menge=1, status="defekt", user_id=1, author_name="EL")
        db.flush()
        assert db.query(LageJournalEntry).filter_by(einheit_id=a.id, ereignis_typ="ausstattung").count() == 2
        assert service.ausstattung_warnungen(db, a) == 1
        with pytest.raises(ValueError, match="Einheit und Lage"):
            service.ausstattung_aendern(db, lage, b, zeile.id, menge=1, user_id=1, author_name="EL")
        service.ausstattung_entfernen(db, lage, a, zeile.id, user_id=1, author_name="EL")
        db.flush()
        assert db.get(LageEinheitAusstattung, zeile.id) is None


def test_vorlage_nimmt_nur_fruehere_eigene_lage():
    with _session() as db:
        org, fremd, lage, _, a, _, _, fahrzeug = _daten(db)
        vorher = MajorIncident(name="Vorher", org_id=org.id)
        fremde_lage = MajorIncident(name="Fremd", org_id=fremd.id)
        db.add_all([vorher, fremde_lage])
        db.flush()
        vorher.started_at = lage.started_at - timedelta(seconds=1)
        quelle = LageEinheit(lage_id=vorher.id, vehicle_id=fahrzeug.id, label="Altes RLF")
        fremde_quelle = LageEinheit(lage_id=fremde_lage.id, vehicle_id=fahrzeug.id, label="Fremdes RLF")
        db.add_all([quelle, fremde_quelle])
        db.flush()
        service.ausstattung_hinzufuegen(
            db, vorher, quelle, kategorie="tauchpumpe", status="defekt", user_id=1, author_name="EL"
        )
        fremde_lage.started_at = lage.started_at - timedelta(milliseconds=500)
        service.ausstattung_hinzufuegen(
            db, fremde_lage, fremde_quelle, kategorie="stromerzeuger", user_id=1, author_name="EL"
        )
        kopien = service.vorlage_uebernehmen(db, lage, a, user_id=1, author_name="EL")
        assert len(kopien) == 1 and kopien[0].bezeichnung == "Tauchpumpe"
        assert kopien[0].status == "einsatzbereit" and kopien[0].stamm_ref_typ == "vorlage_lage"
        with pytest.raises(ValueError, match="bereits"):
            service.vorlage_uebernehmen(db, lage, a, user_id=1, author_name="EL")
        leer = LageEinheit(lage_id=lage.id, label="Leer")
        db.add(leer)
        db.flush()
        with pytest.raises(ValueError, match="kein Fahrzeug"):
            service.vorlage_uebernehmen(db, lage, leer, user_id=1, author_name="EL")
        anderes_fahrzeug = VehicleMaster(dept_id=org.id, code="TLF", name="TLF", type="TLF")
        db.add(anderes_fahrzeug)
        db.flush()
        ohne_vorlage = LageEinheit(lage_id=lage.id, vehicle_id=anderes_fahrzeug.id, label="Ohne Vorlage")
        db.add(ohne_vorlage)
        db.flush()
        with pytest.raises(ValueError, match="Keine Ausstattungsvorlage"):
            service.vorlage_uebernehmen(db, lage, ohne_vorlage, user_id=1, author_name="EL")


def test_umbuchen_teilweise_vollstaendig_merge_und_fehler_atom_ar():
    with _session() as db:
        _, _, lage, andere_lage, a, b, c, _ = _daten(db)
        quelle = service.ausstattung_hinzufuegen(
            db, lage, a, kategorie="tauchpumpe", menge=3, user_id=1, author_name="EL"
        )
        ziel = service.ausstattung_hinzufuegen(
            db, lage, b, kategorie="tauchpumpe", menge=1, user_id=1, author_name="EL"
        )
        service.ausstattung_umbuchen(db, lage, a, b, quelle.id, 2, user_id=1, author_name="EL")
        assert (quelle.menge, ziel.menge) == (1, 3)
        with pytest.raises(ValueError, match="Nicht genügend"):
            service.ausstattung_umbuchen(db, lage, a, b, quelle.id, 2, user_id=1, author_name="EL")
        assert (quelle.menge, ziel.menge) == (1, 3)
        service.ausstattung_umbuchen(db, lage, a, b, quelle.id, 1, user_id=1, author_name="EL")
        db.flush()
        assert db.get(LageEinheitAusstattung, quelle.id) is None and ziel.menge == 4
        with pytest.raises(ValueError, match="Lage"):
            service.ausstattung_umbuchen(db, lage, b, c, ziel.id, 1, user_id=1, author_name="EL")
        assert db.query(LageJournalEntry).filter_by(ereignis_typ="ausstattung", einheit_id=a.id).count() >= 3
        assert db.query(LageJournalEntry).filter_by(ereignis_typ="ausstattung", einheit_id=b.id).count() >= 3
