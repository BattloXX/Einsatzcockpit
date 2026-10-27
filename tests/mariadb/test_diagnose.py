"""Migration 0327 und hostuebergreifende Claim-Serialisierung auf MariaDB."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.models.diagnose import DiagRun
from app.services.diagnose.runner import claim_run

pytestmark = pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL fehlt")


def test_0327_schema_and_parallel_claim():
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    sessions = sessionmaker(engine)
    ident = None
    try:
        schema = inspect(engine)
        for name in ("diag_run", "diag_finding"):
            assert "org_id" not in {c["name"] for c in schema.get_columns(name)}
        assert any(fk["referred_table"] == "diag_run" and fk["options"]["ondelete"] == "CASCADE"
                   for fk in schema.get_foreign_keys("diag_finding"))
        assert any(index["column_names"] == ["active_slot"] and index["unique"]
                   for index in schema.get_indexes("diag_run"))
        with sessions() as db:
            row = DiagRun(checks=["worker.zustand"], umfang={"typ": "voll"},
                          korrelations_id=str(uuid4()), active_slot=1)
            db.add(row)
            db.commit()
            ident = row.id
        barrier = Barrier(4)
        def claim(_):
            with sessions() as db:
                barrier.wait(timeout=10)
                return claim_run(db)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(4)))
        assert results.count(ident) == 1 and results.count(None) == 3
    finally:
        if ident is not None:
            with sessions() as db:
                db.query(DiagRun).filter(DiagRun.id == ident).delete()
                db.commit()
        engine.dispose()
