from __future__ import annotations

import uuid

import pytest

from app.core.tenant import set_tenant_context
from app.models.gateway import (
    JOB_FAILED,
    JOB_SOURCE_MANUAL,
    JOB_SOURCE_RULE,
    Gateway,
    Printer,
    PrintJob,
    PrintRule,
)
from tests.conftest import TestingSession


def _setup(*, source=JOB_SOURCE_RULE, rule_id=True, fallback=True, same=False,
           active=True, fallback_org=None, options=None, fallback_of=None):
    db = TestingSession()
    set_tenant_context(db, None)
    org = 930000 + uuid.uuid4().int % 50000
    gw1 = Gateway(org_id=org, name="GW Ziel")
    gw2 = Gateway(org_id=org, name="GW Ersatz")
    db.add_all([gw1, gw2])
    db.flush()
    ziel = Printer(org_id=org, gateway_id=gw1.id, name="Ziel", uri="ipp://ziel", aktiv=True)
    ersatz = Printer(
        org_id=fallback_org or org, gateway_id=gw2.id, name="Ersatz", uri="ipp://ersatz",
        aktiv=active,
        capabilities={"media": ["A4"]},
    )
    db.add_all([ziel, ersatz])
    db.flush()
    rule = PrintRule(
        org_id=org, name="Regel " + uuid.uuid4().hex, aktiv=True, trigger="einsatz_created",
        documents=["einsatzinfo"], printer_ids=[ziel.id],
        fallback_printer_id=ziel.id if same else ersatz.id if fallback else None,
    )
    db.add(rule)
    db.flush()
    job = PrintJob(
        org_id=org, gateway_id=gw1.id, printer_id=ziel.id, document_type="einsatzinfo",
        source=source, rule_id=rule.id if rule_id else None, status=JOB_FAILED,
        options=options or {}, fallback_of_job_id=fallback_of,
        idempotency_key="fallback-original-" + uuid.uuid4().hex,
    )
    db.add(job)
    db.commit()
    result = (job.id, org, ersatz.id, gw2.id)
    db.close()
    return result


async def test_fallback_happy_path_exactly_once_and_media(monkeypatch):
    import app.services.print_dispatcher as pd

    job_id, org, printer_id, gateway_id = _setup(options={"copies": 2, "media": "A3"})
    sent = []

    async def fake_dispatch(db, job):
        sent.append(job.id)
        return {"status": "sent"}

    monkeypatch.setattr(pd, "dispatch_job", fake_dispatch)
    ersatz = await pd.dispatch_fallback_for_failed_job(job_id)
    assert ersatz is not None
    assert ersatz.org_id == org
    assert ersatz.printer_id == printer_id
    assert ersatz.gateway_id == gateway_id
    assert ersatz.fallback_of_job_id == job_id
    assert "media" not in ersatz.options and ersatz.options["copies"] == 2
    assert sent == [ersatz.id]
    assert await pd.dispatch_fallback_for_failed_job(job_id) is None


@pytest.mark.parametrize("kwargs", [
    {"source": JOB_SOURCE_MANUAL},
    {"rule_id": False},
    {"fallback": False},
    {"same": True},
    {"fallback_of": 123},
])
async def test_fallback_abbruchbedingungen(monkeypatch, kwargs):
    import app.services.print_dispatcher as pd

    job_id, *_ = _setup(**kwargs)
    monkeypatch.setattr(pd, "dispatch_job", pytest.fail)
    assert await pd.dispatch_fallback_for_failed_job(job_id) is None


async def test_fallback_inaktiver_drucker_warnt(caplog):
    import app.services.print_dispatcher as pd

    job_id, *_ = _setup(active=False)
    with caplog.at_level("WARNING"):
        assert await pd.dispatch_fallback_for_failed_job(job_id) is None
    assert "nicht verfuegbar" in caplog.text
