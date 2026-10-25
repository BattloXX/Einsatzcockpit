"""Echte MariaDB-Transaktionen: genau ein Route-Claim und gefencete Reconnects."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.gateway import Gateway, GatewayMessage
from app.models.master import FireDept
from app.models.user import SmsGatewayToken
from app.services import gateway_presence as presence
from app.services import gateway_routing as routing
from tests.gateway_helpers import CaptureSocket

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL nicht gesetzt")


@pytest.mark.parametrize("kind", ["sms", "linux", "windows_agent"])
def test_four_claimants_and_reconnect_fencing_on_mariadb(monkeypatch, kind):
    engine = create_engine(os.environ["MARIADB_TEST_URL"], pool_size=8)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(routing, "SessionLocal", sessions)
    org_id = None
    token_hash = uuid4().hex
    model = SmsGatewayToken if kind == "sms" else Gateway
    connection = None
    try:
        with sessions() as db:
            set_tenant_context(db, None)
            org = FireDept(slug="gateway-phase2-" + uuid4().hex, name="Gateway transaction test")
            db.add(org)
            db.flush()
            org_id = org.id
            row = (
                SmsGatewayToken(org_id=org_id, label="SMS", token_hash=token_hash)
                if kind == "sms"
                else Gateway(org_id=org_id, name="Gateway", kind=kind, device_token_hash=token_hash)
            )
            db.add(row)
            db.commit()
            gateway_id = row.id
        connection = routing.begin(kind, org_id, gateway_id, token_hash)
        socket = CaptureSocket()
        routing.attach(connection, token_hash, socket)
        item_id = routing._enqueue(connection, {"type": "one-send"}, 30, "test-job")
        barrier = Barrier(4)

        def claim(_index):
            barrier.wait(timeout=10)
            return routing._claim(item_id)

        with ThreadPoolExecutor(max_workers=4) as executor:
            winners = list(executor.map(claim, range(4)))
        assert sum(result is not None for result in winners) == 1
        routing.record_result(connection, token_hash, "test-job", {"ok": True})
        assert routing._result(item_id, org_id) == ("sending", {"ok": True})
        barrier = Barrier(4)
        new_ids = [uuid4().hex for _ in range(4)]

        def reconnect(index):
            with sessions() as db:
                set_tenant_context(db, None)
                barrier.wait(timeout=10)
                return presence.begin(db, kind, org_id, gateway_id, token_hash, new_ids[index], f"worker-{index}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            assert all(executor.map(reconnect, range(4)))
        with sessions() as db:
            set_tenant_context(db, None)
            current = presence.connections(db, kind, org_id, gateway_id)[0]
            assert current.connection_id in new_ids
            assert not presence.end(db, kind, org_id, gateway_id, connection.connection_id)
            for old_id in new_ids:
                if old_id != current.connection_id:
                    assert not presence.end(db, kind, org_id, gateway_id, old_id)
                    assert not presence.touch(db, kind, org_id, gateway_id, token_hash, old_id)
            assert not presence.end(db, kind, org_id + 1000, gateway_id, current.connection_id)
            assert presence.describe(db, kind, org_id, gateway_id)["worker_id"] == current.worker_id
            assert presence.describe(db, kind, org_id + 1000, gateway_id) is None
    finally:
        if connection is not None:
            routing.detach(connection)
        if org_id is not None:
            with sessions() as db:
                set_tenant_context(db, None)
                db.query(GatewayMessage).filter(GatewayMessage.org_id == org_id).delete()
                db.query(model).filter(model.org_id == org_id).delete()
                db.query(FireDept).filter(FireDept.id == org_id).delete()
                db.commit()
        engine.dispose()
