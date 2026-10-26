"""Reale Worker-Heartbeats und Alarm-Claims unter konkurrierenden DB-Transaktionen."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentAlarmJob
from app.models.master import FireDept
from app.models.worker_runtime import WorkerRuntime
from app.services import alarm_outbox, background_runtime
from app.services.gateway_presence import utc
from app.services.worker_monitor import snapshot

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


def test_parallel_heartbeats_and_alarm_claims(monkeypatch, tmp_path):
    engine = create_engine(os.environ["MARIADB_TEST_URL"], pool_size=12)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(background_runtime, "SessionLocal", sessions)
    prefix = uuid4().hex
    workers = [background_runtime.BackgroundRuntime(str(tmp_path / "leader"), lambda: [],
                worker_id=f"{prefix}-{number}") for number in range(4)]
    with sessions() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=prefix, name="Background transaction test")
        db.add(org)
        db.flush()
        incident = Incident(primary_org_id=org.id, alarm_type_code="B2", status="active")
        db.add(incident)
        db.flush()
        job = IncidentAlarmJob(org_id=org.id, incident_id=incident.id, channel="teams", status="pending",
                               next_attempt_at=utc(), context={})
        db.add(job)
        db.commit()
        org_id, incident_id, job_id = org.id, incident.id, job.id
    try:
        for worker in workers:
            worker.try_leadership()
        assert sum(w.is_leader for w in workers) == 1
        barrier = Barrier(4)
        def claim(worker):
            barrier.wait(timeout=10)
            worker.write_heartbeat()
            with sessions() as db:
                set_tenant_context(db, None)
                return alarm_outbox._claim(db, db.get(IncidentAlarmJob, job_id), utc())
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(claim, workers)).count(True) == 1
        with sessions() as db:
            set_tenant_context(db, None)
            assert snapshot(db, org_id)["worker_count"] == 4
            assert len(snapshot(db, org_id)["leader_workers"]) == 1
            job = db.get(IncidentAlarmJob, job_id)
            assert job.attempt_count == 1
            job.lease_until = utc() - timedelta(seconds=1)
            db.commit()
            assert not alarm_outbox._claim(db, job, utc())
            db.refresh(job)
            assert job.status == "uncertain" and job.attempt_count == 1
    finally:
        for worker in workers:
            worker.release()
        with sessions() as db:
            set_tenant_context(db, None)
            db.query(WorkerRuntime).filter(WorkerRuntime.worker_id.in_([w.worker_id for w in workers])).delete()
            db.query(IncidentAlarmJob).filter(IncidentAlarmJob.incident_id == incident_id).delete()
            db.query(Incident).filter(Incident.id == incident_id).delete()
            db.query(FireDept).filter(FireDept.id == org_id).delete()
            db.commit()
        engine.dispose()
