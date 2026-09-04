"""Datenabbildung und Wiederaufsetzbarkeit der Migration 0232."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db import Base
import app.models  # noqa: F401


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0232_probenplanung.py"
    spec = importlib.util.spec_from_file_location("migration_0232", path)
    module = importlib.util.module_from_spec(spec); assert spec and spec.loader
    spec.loader.exec_module(module); return module


def test_status_mapping_teilnahme_ableitung_und_idempotenz(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0232.db'}")
    Base.metadata.create_all(engine)
    module = _migration()
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO fire_dept (id,name,slug,color,bos,withdraw_press_factor,withdraw_press_reserve,"
            "escalation_grace_min,is_home_org,is_active,short_code,timezone,created_at) "
            "VALUES (9001,'Test','test-0232','#123456','Feuerwehr',0.5,10,3,0,1,'TST','Europe/Vienna','2026-01-01')"
        ))
        conn.execute(sa.text("INSERT INTO termin (id,org_id,typ,titel,beginn,ganztaegig,status,erstellt_am,public_sichtbar,public_ort_sichtbar,public_info_sichtbar,ics_sequence) VALUES (1,9001,'uebung','Alt','2026-01-01',0,'laufend','2026-01-01',1,0,0,0)"))
        conn.execute(sa.text("INSERT INTO teilnahme (id,org_id,bezug_typ,bezug_id,ausgerueckt,entschuldigt,status,hinzugefuegt_am) VALUES (1,9001,'uebung',1,1,0,'nicht_erfasst','2026-01-01'),(2,9001,'uebung',1,0,1,'nicht_erfasst','2026-01-01'),(3,9001,'uebung',1,0,0,'entschuldigt','2026-01-01')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade(); module.upgrade()
        assert conn.execute(sa.text("SELECT status FROM termin WHERE id=1")).scalar() == "durchfuehrung_laeuft"
        assert conn.execute(sa.text("SELECT status FROM teilnahme ORDER BY id")).scalars().all() == ["anwesend", "entschuldigt", "nicht_erfasst"]
        assert conn.execute(sa.text("SELECT count(*) FROM probeart WHERE org_id=9001")).scalar() == 9
