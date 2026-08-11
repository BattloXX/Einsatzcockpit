import uuid

from app.core.tenant import set_tenant_context
from app.models.gateway import (
    JOB_DONE,
    JOB_PRINTING,
    JOB_SOURCE_MANUAL,
    JOB_SOURCE_RULE,
    PrintJob,
)
from app.services.print_dispatcher import unfulfilled_print_jobs
from tests.conftest import TestingSession


def test_unfulfilled_print_jobs_returns_only_incomplete_rule_jobs(setup_db):
    db = TestingSession()
    set_tenant_context(db, 1)
    incident_id = 987654
    marker = uuid.uuid4().hex
    jobs = [
        PrintJob(
            org_id=1, gateway_id=1, document_type="einsatzinfo",
            source=JOB_SOURCE_RULE, incident_id=incident_id, status=JOB_PRINTING,
            idempotency_key=f"test-open-{marker}",
        ),
        PrintJob(
            org_id=1, gateway_id=1, document_type="einsatzinfo",
            source=JOB_SOURCE_RULE, incident_id=incident_id, status=JOB_DONE,
            idempotency_key=f"test-done-{marker}",
        ),
        PrintJob(
            org_id=1, gateway_id=1, document_type="einsatzinfo",
            source=JOB_SOURCE_MANUAL, incident_id=incident_id, status=JOB_PRINTING,
            idempotency_key=f"test-manual-{marker}",
        ),
    ]
    db.add_all(jobs)
    db.commit()

    try:
        result = unfulfilled_print_jobs(db, incident_id)
        assert [job.id for job in result] == [jobs[0].id]
    finally:
        for job in jobs:
            db.delete(job)
        db.commit()
        db.close()
