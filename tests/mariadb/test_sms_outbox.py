"""SMS-Claims und globale Gateway-Kapazitaet mit echten MariaDB-Zeilensperren."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.gateway import GatewayMessage
from app.models.master import FireDept
from app.models.sms import SmsDispatchAttempt, SmsDispatchCursor, SmsDispatchJob
from app.models.user import SmsGatewayToken
from app.services import gateway_routing as routing
from app.services import sms_outbox as outbox

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


def test_four_workers_claim_once_and_respect_shared_capacity(monkeypatch):
    engine = create_engine(os.environ["MARIADB_TEST_URL"], pool_size=12)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(outbox, "SessionLocal", sessions)
    monkeypatch.setattr(routing, "SessionLocal", sessions)
    with sessions() as db:
        set_tenant_context(db, None)
        org = FireDept(slug="sms-phase3-" + uuid4().hex, name="SMS transaction test")
        db.add(org)
        db.commit()
        org_id = org.id
        gw = SmsGatewayToken(org_id=org_id, token_hash=uuid4().hex, label="Shared SMS")
        db.add(gw)
        db.commit()
        gateway_id, token_hash = gw.id, gw.token_hash
    try:
        assert routing.begin("sms", org_id, gateway_id, token_hash)
        # Gleichzeitige erstmalige Idempotenz-Keys muessen auch bei REPEATABLE READ funktionieren.
        barrier = Barrier(4)

        def enqueue(_):
            barrier.wait(10)
            return outbox.ensure(org_id, "one-job", "+43000", "Alarm", ["gateway"])

        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(enqueue, range(4)))
        assert len(set(ids)) == 1
        barrier = Barrier(4)

        def claim(_):
            barrier.wait(10)
            return outbox.claim(org_id, ids[0])

        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(claim, range(4)))
        assert len([c for c in claims if c]) == 1
        outbox._prepare(org_id, ids[0], next(c for c in claims if c), False, 15)
        pending = [outbox.ensure(org_id, f"many-{i}", f"+4300{i}", "Alarm", ["gateway"]) for i in range(20)]

        def prepare(jid):
            token = outbox.claim(org_id, jid)
            return outbox._prepare(org_id, jid, token, False, 15)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(prepare, pending))
        with outbox.database() as db:
            assert (
                db.query(SmsDispatchJob)
                .filter(SmsDispatchJob.org_id == org_id, SmsDispatchJob.status == "dispatched")
                .count()
                == 7
            )
            assert db.query(GatewayMessage).filter(GatewayMessage.org_id == org_id).count() == 7
            assert (
                db.query(SmsDispatchJob)
                .filter(SmsDispatchJob.org_id == org_id, SmsDispatchJob.status == "queued")
                .count()
                == 14
            )
    finally:
        with outbox.database() as db:
            for model in (GatewayMessage, SmsDispatchAttempt, SmsDispatchJob, SmsDispatchCursor, SmsGatewayToken):
                db.query(model).filter(model.org_id == org_id).delete()
            db.query(FireDept).filter(FireDept.id == org_id).delete()
            db.commit()
        engine.dispose()
