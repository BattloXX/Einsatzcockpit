"""Echte MariaDB-FK-Typen und konkurrierendes Einloesen eines Pairing-Codes."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.db import Base
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
