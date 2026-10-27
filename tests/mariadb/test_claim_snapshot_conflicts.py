"""Deterministische Snapshot-Konflikte, ohne zeitabhaengige Thread-Synchronisation."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.gateway import Gateway, PrintJob
from app.models.incident import Incident, IncidentAlarmJob
from app.models.master import FireDept
from app.services import alarm_outbox
from app.services.gateway_presence import utc
from app.services.print_dispatcher import create_print_job

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


@pytest.fixture
def database():
    engine = create_engine(os.environ["MARIADB_TEST_URL"], isolation_level="REPEATABLE READ")
    sessions = sessionmaker(engine, expire_on_commit=False)

    def session():
        db = sessions()
        set_tenant_context(db, None)
        db.execute(text("SET SESSION innodb_snapshot_isolation=ON"))
        db.commit()
        return db

    with session() as db:
        org = FireDept(name="Snapshot conflict", slug=uuid4().hex)
        db.add(org)
        db.flush()
        gateway = Gateway(org_id=org.id, name="Snapshot", kind="linux", device_token_hash=uuid4().hex)
        incident = Incident(primary_org_id=org.id, alarm_type_code="B2", status="active")
        db.add_all([gateway, incident])
        db.flush()
        job = IncidentAlarmJob(org_id=org.id, incident_id=incident.id, channel="sms", status="pending",
                               next_attempt_at=utc(), context={})
        db.add(job)
        db.commit()
        ids = org.id, gateway.id, incident.id, job.id
    try:
        yield session, ids
    finally:
        with session() as db:
            db.query(PrintJob).filter(PrintJob.org_id == ids[0]).delete()
            db.query(IncidentAlarmJob).filter(IncidentAlarmJob.org_id == ids[0]).delete()
            db.query(Incident).filter(Incident.id == ids[2]).delete()
            db.query(Gateway).filter(Gateway.id == ids[1]).delete()
            db.query(FireDept).filter(FireDept.id == ids[0]).delete()
            db.commit()
        engine.dispose()


def test_server_rejects_locking_read_after_competing_commit(database):
    session, (_, _, _, job_id) = database
    with session() as a:
        assert a.get(IncidentAlarmJob, job_id) is not None
        with session() as b:
            b.execute(update(IncidentAlarmJob).where(IncidentAlarmJob.id == job_id).values(context={"changed": True}))
            b.commit()
        with pytest.raises(OperationalError) as error:
            a.query(IncidentAlarmJob).filter_by(id=job_id).with_for_update().one()
        assert error.value.orig.args[0] == 1020
        a.rollback()


@pytest.mark.parametrize("channel", ["sms", "teams"])
def test_alarm_claim_survives_competing_commit(database, channel):
    session, (_, _, _, job_id) = database
    with session() as b:
        b.execute(update(IncidentAlarmJob).where(IncidentAlarmJob.id == job_id).values(channel=channel))
        b.commit()
    with session() as a:
        job = a.get(IncidentAlarmJob, job_id)
        with session() as b:
            b.execute(update(IncidentAlarmJob).where(IncidentAlarmJob.id == job_id).values(context={"changed": True}))
            b.commit()
        assert alarm_outbox._claim(a, job, utc())
        a.refresh(job)
        assert job.status == "sending" and job.attempt_count == 1
        assert job.context == {"changed": True}
        assert not alarm_outbox._claim(a, job, utc())


def test_print_dedup_survives_winner_outside_snapshot(database):
    session, (org_id, gateway_id, incident_id, _) = database
    params = dict(org_id=org_id, gateway_id=gateway_id, printer_id=None,
                  document_type="einsatzinfo", request_key="same-request")
    with session() as a:
        # Vorherige Aenderungen des Aufrufers duerfen beim Dedup nicht verloren gehen.
        incident = a.get(Incident, incident_id)
        incident.report_text = "Uncommitted caller change"
        a.flush()
        with session() as b:
            winner, created = create_print_job(b, **params)
            assert created
            b.commit()
            winner_id = winner.id
        existing, created = create_print_job(a, **params)
        assert not created and existing.id == winner_id
        assert a.scalar(text("SELECT @@innodb_snapshot_isolation")) == 1
        with session() as b:
            b.execute(text("SET SESSION innodb_lock_wait_timeout=1"))
            with pytest.raises(OperationalError) as error:
                b.execute(update(PrintJob).where(PrintJob.id == winner_id).values(error="locked"))
            assert error.value.orig.args[0] == 1205
            b.rollback()
        a.commit()
    with session() as db:
        assert db.get(Incident, incident_id).report_text == "Uncommitted caller change"
        assert db.query(PrintJob).filter_by(org_id=org_id).count() == 1
