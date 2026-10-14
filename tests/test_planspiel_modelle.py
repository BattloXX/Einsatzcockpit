"""Modelltests fuer die Planspiel-Basis."""

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.planspiel import (
    PlanspielRun,
    PlanspielRunEreignis,
    PlanspielSzenario,
    PlanspielSzenarioEreignis,
    PlanspielSzenarioVersion,
    PlanspielZiel,
)
from tests.conftest import TestingSession


def _org(db, name: str) -> FireDept:
    org = FireDept(slug=f"planspiel-{uuid4().hex}", name=name, timezone="Europe/Vienna")
    db.add(org)
    db.flush()
    return org


def test_planspiel_run_ereignis_ziel_und_tenant_isolation():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org_a = _org(db, "Planspiel Org A")
        org_b = _org(db, "Planspiel Org B")
        db.commit()

        set_tenant_context(db, org_a.id)
        run = PlanspielRun(org_id=org_a.id, titel="Herbstübung", art="einzel", erzeugung="manuell", seed=42)
        db.add(run)
        db.flush()
        db.add(
            PlanspielRunEreignis(
                org_id=org_a.id,
                run_id=run.id,
                seq=1,
                quelle="regie",
                trigger_type="manual",
                idempotenz_key=f"event-{uuid4().hex}",
            )
        )
        db.add(PlanspielZiel(org_id=org_a.id, run_id=run.id, seq=1, typ="incident"))
        incident = Incident(primary_org_id=org_a.id, planspiel_run_id=run.id)
        db.add(incident)
        db.commit()
        assert incident.planspiel_run_id == run.id

        set_tenant_context(db, org_b.id)
        assert db.query(PlanspielRun).filter_by(id=run.id).first() is None
    finally:
        db.close()


def test_planspiel_ziel_seq_und_idempotenz_key_sind_eindeutig():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "Planspiel Constraints")
        db.commit()
        set_tenant_context(db, org.id)
        run = PlanspielRun(org_id=org.id, titel="Constraint-Test", art="einzel", erzeugung="manuell", seed=7)
        db.add(run)
        db.flush()
        db.add(PlanspielZiel(org_id=org.id, run_id=run.id, seq=1, typ="incident"))
        db.commit()

        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(PlanspielZiel(org_id=org.id, run_id=run.id, seq=1, typ="site"))
                db.flush()

        key = f"idempotenz-{uuid4().hex}"
        db.add(
            PlanspielRunEreignis(
                org_id=org.id, run_id=run.id, seq=1, quelle="regie", trigger_type="manual", idempotenz_key=key
            )
        )
        db.commit()
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(
                    PlanspielRunEreignis(
                        org_id=org.id, run_id=run.id, seq=2, quelle="regie", trigger_type="manual", idempotenz_key=key
                    )
                )
                db.flush()
    finally:
        db.close()


def test_szenario_version_ereignis_und_tenant_isolation():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org_a = _org(db, "Katalog Org A")
        org_b = _org(db, "Katalog Org B")
        db.commit()
        set_tenant_context(db, org_a.id)
        szenario = PlanspielSzenario(org_id=org_a.id, code="B09", herkunft="system")
        db.add(szenario)
        db.flush()
        version = PlanspielSzenarioVersion(
            org_id=org_a.id,
            szenario_id=szenario.id,
            version=1,
            titel="Zimmerbrand",
            kategorie="brand",
            stichwort_code="F3",
            alarmtext_template="Zimmerbrand in {adresse}",
            ort_typen=["wohngebaeude"],
            schwierigkeit="mittel",
        )
        db.add(version)
        db.flush()
        db.add(
            PlanspielSzenarioEreignis(
                org_id=org_a.id,
                version_id=version.id,
                ereignis_typ="eskalation",
                trigger_type="manual",
                payload_template="Lageänderung",
            )
        )
        szenario.aktive_version_id = version.id
        db.commit()

        set_tenant_context(db, org_b.id)
        assert db.query(PlanspielSzenario).filter_by(id=szenario.id).first() is None

        set_tenant_context(db, org_a.id)
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(PlanspielSzenario(org_id=org_a.id, code="B09", herkunft="system"))
                db.flush()
    finally:
        db.close()
