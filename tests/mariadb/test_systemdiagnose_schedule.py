"""Migration 0329 und konkurrierende Tagesplaner auf MariaDB."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from app.models.diag_schedule import DiagScheduleSlot
from app.models.diagnose import DiagRun
from app.models.master import SystemSettings
from app.services import diag_schedule

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


def test_0329_schema_parallel_slot_and_downgrade(monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import importlib.util

    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    factory = sessionmaker(engine)
    old = {}
    ids = []
    monkeypatch.setattr(diag_schedule, "einsatz_aktiv", lambda db: False)
    try:
        schema = inspect(engine)
        for table in ("diag_schedule_slot", "diag_notification"):
            assert "org_id" not in {c["name"] for c in schema.get_columns(table)}
        with factory() as db:
            for key, value in diag_schedule.DEFAULTS.items():
                row = db.get(SystemSettings, key)
                old[key] = row.value if row else None
                if row is None:
                    row = SystemSettings(key=key)
                    db.add(row)
                row.value = "true" if key == "diag_schedule_enabled" else value
            db.commit()
        barrier = Barrier(2)
        def claim(_):
            with factory() as db:
                barrier.wait(timeout=10)
                return diag_schedule.schedule_once(db, datetime(2090, 1, 10, 2, 0))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))
        ids = [ident for ident in results if ident is not None]
        assert len(ids) == 1
        with factory() as db:
            assert db.get(DiagScheduleSlot, "2090-01-10").run_id == ids[0]
        spec = importlib.util.spec_from_file_location("migration0329", "alembic/versions/0329_diag_schedule.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Tabellen ausschliesslich dieser Migration leer raeumen und DDL-Roundtrip pruefen.
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.downgrade()
                module.upgrade()
        assert "diag_notification" in inspect(engine).get_table_names()
    finally:
        with factory() as db:
            for slot in db.scalars(select(DiagScheduleSlot).where(DiagScheduleSlot.slot == "2090-01-10")):
                db.delete(slot)
            for ident in ids:
                row = db.get(DiagRun, ident)
                if row:
                    db.delete(row)
            for key, value in old.items():
                row = db.get(SystemSettings, key)
                if value is None:
                    db.delete(row)
                else:
                    row.value = value
            db.commit()
        engine.dispose()
