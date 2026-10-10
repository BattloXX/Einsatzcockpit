"""Regressionen für das tokenfreie QR-Druckartefakt."""

import pytest

from app.models.gateway import DOC_GSL_EINHEIT_QR, PrintJob
from app.services import gk_zugang_service
from app.services.print_artifact_service import ArtifactError, render_job_pdf
from tests.test_gk_qr_zugang import _qr
from tests.test_gk_zugang_service import _session


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _job(org_id, lage_id, einheit_id, generation):
    return PrintJob(
        org_id=org_id, gateway_id=1, source="manual", document_type=DOC_GSL_EINHEIT_QR,
        gsl_id=lage_id, artifact_ref=f"{einheit_id}:{generation}", idempotency_key="manual:test",
    )


def test_qr_renderer_uses_only_image_and_never_pin_or_link(monkeypatch):
    with _session() as db:
        org, lage, einheit, _, access = _qr(db, pin=True)
        captured = {}
        def render(html, base):
            captured["html"] = html
            return b"pdf"

        monkeypatch.setattr("app.services.print_artifact_service._html_to_pdf", render)
        assert render_job_pdf(db, _job(org.id, lage.id, einheit.id, access.generation))
        html = captured["html"]
        access_row = db.get(gk_zugang_service.LageEinheitZugang, access.zugang_id)
        assert gk_zugang_service.qr_pin_fuer_fuehrung(access_row) not in html
        assert access.link not in html


def test_qr_renderer_rejects_rotation_and_wrong_scope():
    with _session() as db:
        org, lage, einheit, _, access = _qr(db)
        job = _job(org.id, lage.id, einheit.id, access.generation)
        gk_zugang_service.stelle_qr_zugang_aus(db, lage, einheit, user_id=None, grund="test", neu=True)
        with pytest.raises(ArtifactError, match="QR-Zugang nicht mehr gültig"):
            render_job_pdf(db, job)
        with pytest.raises(ArtifactError, match="QR-Zugang nicht mehr gültig"):
            render_job_pdf(db, _job(org.id + 999, lage.id, einheit.id, access.generation))
