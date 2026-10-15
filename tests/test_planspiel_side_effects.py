"""Seiteneffekte von Planspiel-Objekten bleiben trotz Org-Freigaben gesperrt."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentAlarmJob, Message
from app.models.major_incident import LageEinheit, MajorIncident, MajorIncidentStatus
from app.models.master import FireDept, OrgSettings, VehicleMaster
from app.models.planspiel import PlanspielRun
from app.models.teams_bot import TeamsAlarmConfig
from app.services.alarm_outbox import CHANNELS, enqueue_incident_alarm
from app.services.gsl_live_notify import _dispatch_gsl_push
from app.services.incident_notify import alarm_unterdrueckung_grund
from app.services.major_incident_service import lage_einrichten
from app.services.task_reminder import _check_due_messages_sync, _notify_due
from tests.conftest import TestingSession

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


def _org_mit_freigaben(db) -> FireDept:
    org = FireDept(slug=f"planspiel-effects-{uuid4().hex}", name="Planspiel Effects")
    db.add(org)
    db.flush()
    settings = OrgSettings(org_id=org.id)
    for flag in ORG_FLAGS:
        setattr(settings, flag, True)
    db.add_all([settings, TeamsAlarmConfig(org_id=org.id, enabled=True, send_exercise=True)])
    db.flush()
    return org


def _run(db, org_id: int) -> PlanspielRun:
    run = PlanspielRun(
        org_id=org_id,
        titel="Seiteneffekt-Test",
        art="einzel",
        erzeugung="manuell",
        seed=1,
        status="laeuft",
    )
    db.add(run)
    db.flush()
    return run


def test_planspiel_incident_alarm_jobs_sind_suppressed_und_normale_uebung_bleibt_unveraendert(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org_mit_freigaben(db)
        run = _run(db, org.id)
        incident = Incident(
            primary_org_id=org.id,
            is_exercise=True,
            planspiel_run_id=run.id,
            alarm_type_code="T1",
        )
        db.add(incident)
        db.flush()

        assert alarm_unterdrueckung_grund(db, incident, org.id) == "Planspiel"
        enqueue_incident_alarm(db, incident, org_id=org.id, source="test")
        db.flush()
        jobs = db.query(IncidentAlarmJob).filter(IncidentAlarmJob.incident_id == incident.id).all()
        assert {job.channel for job in jobs} == set(CHANNELS)
        assert {job.status for job in jobs} == {"suppressed"}

        normal = Incident(primary_org_id=org.id, is_exercise=True, alarm_type_code="T1")
        db.add(normal)
        db.flush()
        assert alarm_unterdrueckung_grund(db, normal, org.id) is None
    finally:
        db.rollback()
        db.close()


def test_planspiel_gsl_live_push_bleibt_gesperrt_normale_uebung_nicht(monkeypatch, setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org_mit_freigaben(db)
        run = _run(db, org.id)
        planspiel_lage = MajorIncident(
            org_id=org.id,
            name="Planspiel-Lage",
            status=MajorIncidentStatus.active,
            is_exercise=True,
            planspiel_run_id=run.id,
        )
        normale_lage = MajorIncident(
            org_id=org.id,
            name="Normale Uebung",
            status=MajorIncidentStatus.active,
            is_exercise=True,
        )
        db.add_all([planspiel_lage, normale_lage])
        db.commit()
        calls = []
        monkeypatch.setattr(
            "app.services.push_service.notify_org_web", lambda *args, **kwargs: calls.append(args)
        )

        _dispatch_gsl_push(planspiel_lage.id, org.id, "created")
        assert calls == []
        _dispatch_gsl_push(normale_lage.id, org.id, "created")
        assert len(calls) == 1
    finally:
        db.close()


def test_task_reminder_sperrt_nur_planspiel_push_und_broadcastet_weiter(monkeypatch, setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org_mit_freigaben(db)
        run = _run(db, org.id)
        planspiel = Incident(
            primary_org_id=org.id,
            is_exercise=True,
            planspiel_run_id=run.id,
            incident_leader_user_id=101,
            alarm_type_code="T1",
        )
        normal = Incident(
            primary_org_id=org.id,
            is_exercise=True,
            incident_leader_user_id=102,
            alarm_type_code="T1",
        )
        db.add_all([planspiel, normal])
        db.flush()
        due_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        db.add_all([
            Message(incident_id=planspiel.id, title="Planspiel", due_at=due_at),
            Message(incident_id=normal.id, title="Normal", due_at=due_at),
        ])
        db.commit()
        broadcasts = []
        pushes = []

        async def broadcast(incident_id, payload):
            broadcasts.append((incident_id, payload))

        monkeypatch.setattr("app.services.task_reminder.manager.broadcast", broadcast)
        monkeypatch.setattr(
            "app.services.push_service.notify_user", lambda *args, **kwargs: pushes.append(args)
        )

        # Andere Tests desselben Workers koennen weitere faellige Meldungen
        # hinterlassen; ausgewertet werden nur die beiden eigenen Einsaetze.
        eigene = {planspiel.id, normal.id}
        due = [item for item in _check_due_messages_sync(db) if item["incident_id"] in eigene]
        for item in due:
            asyncio.run(_notify_due(item))

        assert {incident_id for incident_id, _ in broadcasts} == eigene
        assert [call[1] for call in pushes] == [102]
    finally:
        db.close()


def test_lage_einrichten_kopiert_pin_und_seedt_aktive_fahrzeuge(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org_mit_freigaben(db)
        settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).one()
        settings.default_access_pin_hash = "pin-hash"
        first = VehicleMaster(dept_id=org.id, code="B", name="Beta", display_order=2, active=True)
        second = VehicleMaster(dept_id=org.id, code="A", name="Alpha", display_order=1, active=True)
        inactive = VehicleMaster(dept_id=org.id, code="C", name="Gamma", active=False)
        db.add_all([first, second, inactive])
        db.flush()
        lage = MajorIncident(org_id=org.id, name="Neue Lage", status=MajorIncidentStatus.active)
        db.add(lage)
        db.flush()

        lage_einrichten(db, lage, org_id=org.id, user_id=None)
        db.flush()

        einheiten = db.query(LageEinheit).filter(LageEinheit.lage_id == lage.id).all()
        assert lage.access_pin_hash == "pin-hash"
        assert [einheit.vehicle_id for einheit in einheiten] == [second.id, first.id]
        assert all(einheit.status == "bereitgestellt" for einheit in einheiten)
    finally:
        db.rollback()
        db.close()
