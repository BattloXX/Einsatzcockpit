from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import Gateway, PrintJob
from app.routers.gateway_api import get_artifact


@pytest.mark.parametrize("code", [1020, 1213, 2006])
def test_only_idempotent_artifact_render_retries_known_transaction_conflicts(monkeypatch, code):
    db = SessionLocal()
    set_tenant_context(db, None)
    gateway = Gateway(org_id=1, name="Render retry", kind="linux")
    db.add(gateway)
    db.flush()
    job = PrintJob(org_id=1, gateway_id=gateway.id, document_type="einsatzinfo", idempotency_key=uuid4().hex)
    db.add(job)
    db.commit()
    job_id, gateway_id = job.id, gateway.id
    calls = []

    def render(session, current):
        calls.append(current.id)
        if len(calls) == 1:
            raise OperationalError("SELECT private_data", {}, Exception(code, "test-only conflict"))
        return b"%PDF-test"

    monkeypatch.setattr("app.services.print_artifact_service.verify_artifact_token", lambda *_: 1)
    monkeypatch.setattr("app.services.print_artifact_service.render_job_pdf", render)
    try:
        if code == 2006:
            with pytest.raises(OperationalError):
                get_artifact(job_id, "test-only", db)
            assert len(calls) == 1
        else:
            response = get_artifact(job_id, "test-only", db)
            assert response.body == b"%PDF-test" and len(calls) == 2
        assert db.get(PrintJob, job_id).status == "queued"
    finally:
        db.rollback()
        db.query(PrintJob).filter(PrintJob.id == job_id).delete()
        db.query(Gateway).filter(Gateway.id == gateway_id).delete()
        db.commit()
        db.close()


def test_artifact_conflict_retries_are_bounded(monkeypatch):
    from unittest.mock import MagicMock

    db = MagicMock()
    job = MagicMock(org_id=1)
    db.get.return_value = job
    calls = []

    def render(*_):
        calls.append(1)
        raise OperationalError("SELECT", {}, Exception(1020, "test-only"))

    monkeypatch.setattr("app.services.print_artifact_service.verify_artifact_token", lambda *_: 1)
    monkeypatch.setattr("app.services.print_artifact_service.render_job_pdf", render)
    with pytest.raises(OperationalError):
        get_artifact(1, "test-only", db)
    assert len(calls) == 3 and db.rollback.call_count == 2
