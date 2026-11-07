"""Echte MariaDB-FK-Typen und konkurrierendes Einloesen eines Pairing-Codes."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base, _set_db_timezone_utc
from app.models.infoscreen_agent import InfoscreenDevice
from app.models.master import FireDept
from app.services import infoscreen_agent_service as service

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL nicht gesetzt")


def test_migriertes_schema_und_pairing_einmalig():
    engine = sa.create_engine(os.environ["MARIADB_TEST_URL"])
    sessions = sessionmaker(engine)
    org_id = device_id = None
    try:
        inspector = sa.inspect(engine)
        for name, table in Base.metadata.tables.items():
            if not name.startswith("infoscreen_") or name == "infoscreen_url":
                continue
            columns = {c["name"]: c for c in inspector.get_columns(name)}
            assert set(columns) == set(table.columns.keys())
            for fk in table.foreign_keys:
                target = next(c for c in inspector.get_columns(fk.column.table.name) if c["name"] == fk.column.name)
                assert str(columns[fk.parent.name]["type"]) == str(target["type"])
        with sessions() as db:
            set_tenant_context(db, None)
            org = FireDept(slug="infoscreen-" + uuid4().hex, name="Infoscreen S1 Test")
            db.add(org)
            db.flush()
            org_id = org.id
            device = InfoscreenDevice(org_id=org.id, name="Test")
            db.add(device)
            db.flush()
            device_id = device.id
            code = service.erzeuge_pairing_code(db, device)
            db.commit()
        barrier = Barrier(4)

        def pair_once(_index):
            with sessions() as db:
                set_tenant_context(db, None)
                barrier.wait(timeout=10)
                result = service.pair(db, code)
                db.commit()
                return result is not None

        with ThreadPoolExecutor(max_workers=4) as executor:
            assert sum(executor.map(pair_once, range(4))) == 1
    finally:
        with sessions() as db:
            set_tenant_context(db, None)
            if device_id is not None:
                db.delete(db.get(InfoscreenDevice, device_id))
            if org_id is not None:
                db.delete(db.get(FireDept, org_id))
            db.commit()
        engine.dispose()


def test_update_check_legt_neue_version_nur_einmal_an():
    """Mehrere Geraete melden dieselbe neue Version gleichzeitig: genau eine Zeile (Mutex + UNIQUE)."""
    from app.models.infoscreen_agent import InfoscreenRelease
    from app.services import infoscreen_agent_update_check as update_check

    engine = sa.create_engine(os.environ["MARIADB_TEST_URL"])
    # Wie die App-Engine: Snapshot-Isolation (MariaDB >= 11.6.2) per Connect-Listener abschalten.
    sa.event.listen(engine, "connect", _set_db_timezone_utc)
    sessions = sessionmaker(engine)
    org_id = None
    device_ids: list[int] = []
    version = f"2026.12.{uuid4().int % 10**6}"
    try:
        with sessions() as db:
            set_tenant_context(db, None)
            org = FireDept(slug="infoscreen-" + uuid4().hex, name="Infoscreen Update Test")
            db.add(org)
            db.flush()
            org_id = org.id
            for index in range(4):
                device = InfoscreenDevice(
                    org_id=org.id, name=f"Test {index}", channel="stable", agent_version="2026.1.1")
                db.add(device)
                db.flush()
                device_ids.append(device.id)
            db.commit()
        barrier = Barrier(4)

        def check_once(device_id):
            with sessions() as db:
                set_tenant_context(db, None)
                device = db.get(InfoscreenDevice, device_id)
                barrier.wait(timeout=10)
                result = update_check.check(db, device, version, "stable", "amd64", "2026.1.1")
                db.commit()
                return result["reason"]

        with ThreadPoolExecutor(max_workers=4) as executor:
            reasons = list(executor.map(check_once, device_ids))
        assert all(reason in ("rollout", "unknown", "incident", "ok", "window") for reason in reasons)
        with sessions() as db:
            set_tenant_context(db, None)
            rows = db.query(InfoscreenRelease).filter_by(version=version, channel="stable", arch="amd64").all()
            assert len(rows) == 1
    finally:
        with sessions() as db:
            set_tenant_context(db, None)
            for row in db.query(InfoscreenRelease).filter_by(version=version).all():
                db.delete(row)
            for device_id in device_ids:
                device = db.get(InfoscreenDevice, device_id)
                if device is not None:
                    db.delete(device)
            if org_id is not None:
                db.delete(db.get(FireDept, org_id))
            db.commit()
        engine.dispose()
