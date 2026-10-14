"""Tests fuer den versionierten Planspiel-Katalog-Service."""

from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.master import AlarmType, FireDept
from app.models.planspiel import PlanspielRun, PlanspielSzenario, PlanspielZiel
from app.services import planspiel_katalog_service as service
from tests.conftest import TestingSession


def _org(db, name: str) -> FireDept:
    org = FireDept(slug=f"katalog-{uuid4().hex}", name=name, timezone="Europe/Vienna")
    db.add(org)
    db.flush()
    return org


def test_import_ist_idempotent_und_legt_versionen_an(monkeypatch):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "Katalog")
        db.commit()
        set_tenant_context(db, org.id)
        ergebnis = service.katalog_importieren(db, org.id)
        assert len(ergebnis.neu) == 57
        db.flush()
        szenarien = db.query(PlanspielSzenario).filter(PlanspielSzenario.org_id == org.id).all()
        assert len(szenarien) == 57
        assert all(s.aktive_version_id for s in szenarien)
        versionen = {s.code: service.aktive_version(db, s).version for s in szenarien}
        assert service.katalog_importieren(db, org.id).neu == []
        assert {s.code: service.aktive_version(db, s).version for s in szenarien} == versionen
    finally:
        db.close()


def test_versionierung_reset_export_und_schutz_vor_fremden_orgs(monkeypatch):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "A")
        fremd = _org(db, "B")
        db.commit()
        set_tenant_context(db, org.id)
        service.katalog_importieren(db, org.id)
        szenario = db.query(PlanspielSzenario).filter(
            PlanspielSzenario.org_id == org.id, PlanspielSzenario.code == "B09"
        ).one()
        inhalt = service.version_als_dict(service.aktive_version(db, szenario))
        inhalt["titel"] = "Eigene Anpassung"
        service.neue_version(db, szenario, inhalt, None)
        assert szenario.org_geaendert
        vorher = service.aktive_version(db, szenario).version
        service.auf_standard_zuruecksetzen(db, szenario, None)
        assert not szenario.org_geaendert
        assert service.aktive_version(db, szenario).version == vorher + 1
        export = service.export_json(db, org.id, ["B09"])
        set_tenant_context(db, fremd.id)
        assert service.szenarien_fuer_stichwort(db, fremd.id, "F3") == []
        service.import_json(db, fremd.id, export, None)
        assert len(service.szenarien_fuer_stichwort(db, fremd.id, "F3")) == 1
    finally:
        db.close()


def test_rendern_validierung_stichwort_und_entfernen():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "Katalog")
        db.commit()
        set_tenant_context(db, org.id)
        service.katalog_importieren(db, org.id)
        assert service.rendern("Brand {adresse} {x.y} {__class__}", {"adresse": "<b>Hauptstr. 1</b>"}) == (
            "Brand <b>Hauptstr. 1</b> {x.y} {__class__}"
        )
        assert service.unbekannte_platzhalter("{__class__}") == {"__class__"}
        db.add(AlarmType(org_id=org.id, code="f3", category="F", label="Brand"))
        db.flush()
        assert service.org_stichwort_code(db, org.id, "F3") == "f3"
        vorschlag = service.stichwort_vorschlag(db, org.id, "Rauch aus Fenster im Mehrfamilienhaus")
        assert "F3" in [code for code, _ in vorschlag[:2]]
        szenario = db.query(PlanspielSzenario).filter(
            PlanspielSzenario.org_id == org.id, PlanspielSzenario.code == "B01"
        ).one()
        assert service.entfernen(db, szenario) == "geloescht"
        benutzt = db.query(PlanspielSzenario).filter(
            PlanspielSzenario.org_id == org.id, PlanspielSzenario.code == "B02"
        ).one()
        run = PlanspielRun(org_id=org.id, titel="Test", art="einzel", erzeugung="manuell", seed=1)
        db.add(run)
        db.flush()
        db.add(PlanspielZiel(org_id=org.id, run_id=run.id, seq=999, typ="incident", szenario_code="B02"))
        assert service.entfernen(db, benutzt) == "archiviert"
    finally:
        db.close()


def test_import_validiert_vor_mutation():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "Katalog")
        db.commit()
        set_tenant_context(db, org.id)
        payload = {"format": "ecp-planspiel-katalog", "schema_version": 1, "szenarien": [{
            "code": "X", "aktiv": True, "zufall_erlaubt": True, "gewicht": 1,
            "version": {**service.system_inhalt("B01"), "alarmtext_template": "{__class__}"},
        }]}
        with pytest.raises(ValueError, match="version.alarmtext_template"):
            service.import_json(db, org.id, payload, None)
        assert db.query(PlanspielSzenario).filter(PlanspielSzenario.org_id == org.id).count() == 0
    finally:
        db.close()
