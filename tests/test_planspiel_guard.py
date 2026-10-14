"""Tests fuer die Planspiel-Sperre des zentralen External-Guards."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.models.master import FireDept, OrgSettings
from app.models.planspiel import PlanspielRun
from app.services.exercise_guard import ExerciseChannel, darf_extern
from tests.conftest import TestingSession

ROOT = Path(__file__).resolve().parents[1]
KANAELE = get_args(ExerciseChannel)
ORG_FLAGS = (
    "uebung_push_erlaubt",
    "uebung_ws_alarm_erlaubt",
    "uebung_nachbar_einladung_erlaubt",
    "uebung_lis_status_erlaubt",
    "uebung_wordpress_bericht_erlaubt",
    "uebung_geocoding_erlaubt",
    "einsatzinfo_sms_send_exercise",
    "einsatzinfo_mail_send_exercise",
    "gk_sms_uebung",
)


def _org(db, name: str) -> FireDept:
    org = FireDept(slug=f"planspiel-guard-{uuid4().hex}", name=name)
    db.add(org)
    db.flush()
    return org


def _run(db, org_id: int, freigaben: dict | None = None) -> PlanspielRun:
    run = PlanspielRun(
        org_id=org_id,
        titel="Guard-Test",
        art="einzel",
        erzeugung="manuell",
        seed=1,
        status="laeuft",
        kanal_freigaben=freigaben,
    )
    db.add(run)
    db.flush()
    return run


def test_alle_darf_extern_aufrufe_uebergeben_planspiel_run_id():
    fehlende = []
    for path in ROOT.joinpath("app").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "darf_extern":
                continue
            if not any(keyword.arg == "planspiel_run_id" for keyword in node.keywords):
                fehlende.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not fehlende, "planspiel_run_id fehlt bei: " + ", ".join(fehlende)


def test_planspiel_run_ignoriert_org_uebungsfreigaben(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "Planspiel Guard")
        settings = OrgSettings(org_id=org.id)
        for flag in ORG_FLAGS:
            setattr(settings, flag, True)
        db.add(settings)
        db.flush()
        set_tenant_context(db, org.id)
        run = _run(db, org.id)

        for kanal in KANAELE:
            assert darf_extern(
                kanal, is_exercise=True, org_id=org.id, db=db, planspiel_run_id=run.id
            ) is False
        for kanal in ("push", "ws_alarm", "nachbar", "lis_status", "wordpress", "geocoding", "sms", "mail", "gk_sms"):
            assert darf_extern(
                kanal, is_exercise=True, org_id=org.id, db=db, planspiel_run_id=None
            ) is True
    finally:
        db.rollback()
        db.close()


def test_planspiel_run_gibt_nur_explizit_freigegebenen_kanal_frei(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "Planspiel Freigabe")
        db.add(OrgSettings(org_id=org.id))
        db.flush()
        set_tenant_context(db, org.id)
        run = _run(db, org.id, {"sms": True})

        for kanal in KANAELE:
            assert darf_extern(
                kanal, is_exercise=True, org_id=org.id, db=db, planspiel_run_id=run.id
            ) is (kanal == "sms")
    finally:
        db.rollback()
        db.close()


def test_planspiel_run_ist_fail_closed_fuer_fremde_und_fehlende_runs(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "Planspiel Eigene Org")
        fremde_org = _org(db, "Planspiel Fremde Org")
        db.add_all([OrgSettings(org_id=org.id), OrgSettings(org_id=fremde_org.id)])
        db.flush()
        set_tenant_context(db, fremde_org.id)
        fremder_run = _run(db, fremde_org.id, {"sms": True})
        set_tenant_context(db, org.id)
        eigener_run = _run(db, org.id)

        assert darf_extern(
            "sms", is_exercise=True, org_id=org.id, db=db, planspiel_run_id=fremder_run.id
        ) is False
        assert darf_extern(
            "sms", is_exercise=True, org_id=org.id, db=db, planspiel_run_id=999999
        ) is False
        assert darf_extern(
            "sms", is_exercise=False, org_id=org.id, db=db, planspiel_run_id=eigener_run.id
        ) is False
    finally:
        db.rollback()
        db.close()
