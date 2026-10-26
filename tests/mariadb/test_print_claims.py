"""Echte konkurrierende Worker: Linux-Transport und Windows-Leases."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.models.gateway import Gateway, GatewayMessage, Printer, PrinterAgentRoute, PrintJob
from app.models.master import FireDept
from app.services import gateway_presence as presence
from app.services import linux_print_service as linux
from app.services import print_agent_service as windows

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


def test_four_workers_claim_linux_and_windows_once(monkeypatch):
    engine = create_engine(os.environ["MARIADB_TEST_URL"], pool_size=8)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(linux, "SessionLocal", sessions)

    def database():
        db = sessions()
        set_tenant_context(db, None)
        return db

    now = presence.utc()
    with database() as db:
        org = FireDept(name="Phase 4 claim test", slug="print-" + uuid4().hex)
        db.add(org)
        db.flush()
        org_id = org.id
        gateways = [
            Gateway(org_id=org_id, name=kind + str(i), kind=kind, device_token_hash=uuid4().hex)
            for i, kind in enumerate(("linux", "windows_agent", "windows_agent"))
        ]
        db.add_all(gateways)
        db.commit()
        gids = [g.id for g in gateways]
        for g in gateways:
            presence.begin(db, g.kind, org_id, g.id, g.device_token_hash, uuid4().hex)
        printers = [Printer(org_id=org_id, gateway_id=gids[i], name=str(i), uri="ipp://test") for i in (0, 1)]
        db.add_all(printers)
        db.flush()
        pids = [p.id for p in printers]
        for index in (1, 2):
            db.add(
                PrinterAgentRoute(
                    org_id=org_id,
                    printer_id=pids[1],
                    gateway_id=gids[index],
                    local_queue="Shared network printer",
                    priority=index * 10,
                    ready=True,
                    readiness={"checked_at": now.isoformat()},
                    identity_check=False,
                )
            )
        jobs = [
            PrintJob(
                org_id=org_id,
                gateway_id=gids[i],
                printer_id=pids[i],
                document_type="einsatzinfo",
                delivery="push" if i == 0 else "lease",
                queued_at=now - timedelta(seconds=60),
                idempotency_key=uuid4().hex,
            )
            for i in (0, 1)
        ]
        db.add_all(jobs)
        db.commit()
        ids = [j.id for j in jobs]
    try:
        from app.services.print_dispatcher import create_print_job

        barrier = Barrier(4)

        def create(_):
            with database() as db:
                barrier.wait(10)
                job, created = create_print_job(
                    db,
                    org_id=org_id,
                    gateway_id=gids[0],
                    printer_id=None,
                    document_type="einsatzinfo",
                    request_key="phase4-same-request",
                )
                db.commit()
                return job.id, created

        with ThreadPoolExecutor(max_workers=4) as pool:
            creations = list(pool.map(create, range(4)))
        assert len({jid for jid, _ in creations}) == 1
        assert sum(created for _, created in creations) == 1
        barrier = Barrier(4)

        def claim_linux(_):
            barrier.wait(10)
            return linux.prepare(org_id, ids[0], {"job_id": ids[0]})

        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(claim_linux, range(4)))
        assert sum(c is not None for c in claims) == 1
        barrier = Barrier(4)

        def claim_windows(index):
            with database() as db:
                gw = db.get(Gateway, gids[1 + index % 2])
                barrier.wait(10)
                return windows.lease_jobs(db, gw, 1, now)

        with ThreadPoolExecutor(max_workers=4) as pool:
            grants = list(pool.map(claim_windows, range(4)))
        assert sum(len(g) for g in grants) == 1
        with database() as db:
            assert db.query(GatewayMessage).filter_by(org_id=org_id).count() == 1
            assert db.get(PrintJob, ids[0]).attempts == 1
            win = db.get(PrintJob, ids[1])
            assert win.lease_generation == 1 and win.attempts == 1
    finally:
        with database() as db:
            for model in (GatewayMessage, PrintJob, PrinterAgentRoute, Printer, Gateway):
                db.query(model).filter(model.org_id == org_id).delete()
            db.query(FireDept).filter(FireDept.id == org_id).delete()
            db.commit()
        engine.dispose()
