from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.core.security import sign_artifact_token
from app.core.tenant import set_tenant_context
from app.models.gateway import JOB_CANCELED, JOB_FAILED, JOB_SENT, PrintJob
from tests.conftest import TestingSession


def _job(document_type: str, status: str = JOB_SENT):
    db = TestingSession()
    set_tenant_context(db, None)
    job = PrintJob(
        org_id=1, gateway_id=1, document_type=document_type, status=status,
        idempotency_key="render-failure-" + uuid.uuid4().hex,
    )
    db.add(job)
    db.commit()
    return db, job


def test_artifact_renderfehler_persistiert_und_ist_idempotent():
    from app.routers.gateway_api import get_artifact

    db, job = _job("gsl_lageblatt")
    sig = sign_artifact_token(job.id, job.org_id)
    try:
        with pytest.raises(HTTPException) as exc:
            get_artifact(job.id, sig, db)
        assert exc.value.status_code == 422
        db.refresh(job)
        assert job.status == JOB_FAILED
        assert "Rendern fehlgeschlagen" in job.error
        fehler = job.error
        with pytest.raises(HTTPException):
            get_artifact(job.id, sig, db)
        db.refresh(job)
        assert job.error == fehler
    finally:
        db.close()


def test_artifact_renderfehler_laesst_abbruch_unveraendert():
    from app.routers.gateway_api import get_artifact

    db, job = _job("gsl_lageblatt", JOB_CANCELED)
    try:
        with pytest.raises(HTTPException):
            get_artifact(job.id, sign_artifact_token(job.id, job.org_id), db)
        db.refresh(job)
        assert job.status == JOB_CANCELED
    finally:
        db.close()


def test_html_renderfehler_persistiert(monkeypatch):
    from app.routers.gateway_api import get_render_page
    from app.services.print_artifact_service import ArtifactError

    db, job = _job("lage_karte")
    monkeypatch.setattr(
        "app.services.print_artifact_service.render_map_html",
        lambda db, job: (_ for _ in ()).throw(ArtifactError("Karte fehlt")),
    )
    try:
        with pytest.raises(HTTPException) as exc:
            get_render_page(job.id, sign_artifact_token(job.id, job.org_id), db)
        assert exc.value.status_code == 422
        db.refresh(job)
        assert job.status == JOB_FAILED and "Karte fehlt" in job.error
    finally:
        db.close()
