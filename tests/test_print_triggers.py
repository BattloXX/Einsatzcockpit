from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.models.gateway import (
    DOC_GSL_BERICHT,
    DOC_OBJEKTBLATT,
    TRIGGER_DOCUMENT_TYPES,
    TRIGGER_EINSATZ_UPDATED,
    TRIGGER_GSL_LAGE_UPDATED,
    AlarmIngest,
    Gateway,
    PrintJob,
    PrintRule,
)
from app.models.master import OrgSettings, SystemSettings
from tests.conftest import TestingSession


@pytest.fixture
def db(setup_db):
    session = TestingSession()
    set_tenant_context(session, None)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _enable(db, org_id: int) -> Gateway:
    flag = db.query(SystemSettings).filter(
        SystemSettings.key == "gateway_module_enabled"
    ).first()
    if flag is None:
        db.add(SystemSettings(key="gateway_module_enabled", value="true"))
    else:
        flag.value = "true"
    db.add(OrgSettings(org_id=org_id, gateway_module_enabled=True))
    gateway = Gateway(
        org_id=org_id,
        name="Trigger-Gateway",
        device_token_hash=hash_api_key(uuid.uuid4().hex),
    )
    db.add(gateway)
    db.flush()
    return gateway


def test_updated_trigger_document_types_are_enabled():
    assert DOC_OBJEKTBLATT in TRIGGER_DOCUMENT_TYPES[TRIGGER_EINSATZ_UPDATED]
    assert DOC_GSL_BERICHT in TRIGGER_DOCUMENT_TYPES[TRIGGER_GSL_LAGE_UPDATED]


def test_einsatz_updated_prints_only_newly_confirmed_object(db):
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, Objekt, ObjektEinsatz
    from app.services.print_dispatcher import on_event

    org = 992101
    gateway = _enable(db, org)
    rule = PrintRule(
        org_id=org,
        name="Objekt-Nachzuegler",
        aktiv=True,
        trigger=TRIGGER_EINSATZ_UPDATED,
        documents=[DOC_OBJEKTBLATT],
        printer_ids=[gateway.id],
    )
    objekt = Objekt(org_id=org, nummer="N-1", name="Nachzuegler")
    db.add_all([rule, objekt])
    db.flush()

    assert on_event(db, org, TRIGGER_EINSATZ_UPDATED, {"incident_id": 88101}) == []

    db.add(ObjektEinsatz(
        org_id=org,
        objekt_id=objekt.id,
        incident_id=88101,
        quelle="manuell",
        status=OBJEKT_EINSATZ_BESTAETIGT,
    ))
    db.flush()
    jobs = on_event(db, org, TRIGGER_EINSATZ_UPDATED, {"incident_id": 88101})
    assert len(jobs) == 1 and jobs[0].objekt_id == objekt.id
    assert on_event(db, org, TRIGGER_EINSATZ_UPDATED, {"incident_id": 88101}) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("dedup_action", ["created", "merged"])
async def test_alarm_background_creates_one_rawtext_job(db, monkeypatch, dedup_action):
    import app.services.print_dispatcher as dispatcher

    org = 992200 + (1 if dedup_action == "created" else 2)
    gateway = _enable(db, org)
    rule = PrintRule(
        org_id=org,
        name="Alarm-Rohtext " + dedup_action,
        aktiv=True,
        trigger="alarm_serial_received",
        documents=["alarm_rohtext"],
        printer_ids=[gateway.id],
    )
    ingest = AlarmIngest(
        org_id=org,
        gateway_id=gateway.id,
        raw_hash=uuid.uuid4().hex,
        raw_text="ALARM " + dedup_action,
        parse_status="parsed",
        dedup_action=dedup_action,
    )
    db.add_all([rule, ingest])
    db.commit()
    ingest_id = ingest.id

    sent = []

    async def fake_dispatch(_db, job):
        sent.append(job.id)
        return {"status": "sent"}

    monkeypatch.setattr(dispatcher, "dispatch_job", fake_dispatch)
    await dispatcher.autoprint_alarm_background(ingest_id)

    db.expire_all()
    jobs = db.query(PrintJob).filter(PrintJob.org_id == org).all()
    assert len(jobs) == 1
    assert jobs[0].artifact_ref == str(ingest_id)
    assert sent == [jobs[0].id]


class _AlarmRequest:
    base_url = "http://testserver/"

    def __init__(self, raw_text: str):
        self.headers = {"authorization": "Bearer test"}
        self._raw_text = raw_text

    async def json(self):
        return {"raw_text": self._raw_text}


@pytest.mark.parametrize(
    ("created", "dedup_action", "scheduled"),
    [(True, "created", True), (True, "merged", True), (False, "created", False)],
)
def test_alarm_route_schedules_trigger_only_for_new_ingest(
    monkeypatch, created, dedup_action, scheduled,
):
    import app.routers.gateway_api as api
    import app.services.serial_alarm_service as serial
    from app.services.print_dispatcher import autoprint_alarm_background

    gateway = SimpleNamespace(id=9, org_id=7)
    ingest = SimpleNamespace(
        id=11,
        einsatz_id=12,
        parse_status="parsed",
        process_status="applied",
        dedup_action=dedup_action,
    )
    db = MagicMock(info={})
    db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(api, "_resolve_gateway_from_bearer", lambda _request, _db: gateway)
    monkeypatch.setattr(serial, "store_raw", lambda *_args, **_kwargs: (ingest, created))
    monkeypatch.setattr(serial, "process_ingest", lambda *_args, **_kwargs: ingest)
    tasks = BackgroundTasks()

    api.ingest_alarm(_AlarmRequest(uuid.uuid4().hex), tasks, db)

    scheduled_functions = [task.func for task in tasks.tasks]
    assert (autoprint_alarm_background in scheduled_functions) is scheduled
