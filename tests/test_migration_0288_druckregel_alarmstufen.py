"""Wiederaufsetzbarkeit der Migration von Mindest- zu Code-Alarmstufen."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0288_druckregel_alarmstufen.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0288", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _lauf(conn, funktion):
    with Operations.context(MigrationContext.configure(conn)):
        funktion()


def test_upgrade_ersetzt_minimum_je_org_und_ist_idempotent(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0288.db'}")
    migration = _migration()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE print_rule (id INTEGER PRIMARY KEY, org_id INTEGER, filters TEXT)")
        conn.exec_driver_sql("CREATE TABLE alarm_type (id INTEGER PRIMARY KEY, org_id INTEGER, code TEXT)")
        conn.execute(sa.text("INSERT INTO print_rule VALUES (1, 1, :filters), (2, 2, :filters)"), {
            "filters": json.dumps({"min_alarmstufe": 3}),
        })
        for nummer, code in enumerate(["F1", "F3", "F14", "T1", "T4"], 1):
            conn.execute(sa.text("INSERT INTO alarm_type VALUES (:id, 1, :code)"), {"id": nummer, "code": code})
        _lauf(conn, migration.upgrade)
        _lauf(conn, migration.upgrade)
        erste = json.loads(conn.execute(sa.text("SELECT filters FROM print_rule WHERE id=1")).scalar_one())
        zweite = json.loads(conn.execute(sa.text("SELECT filters FROM print_rule WHERE id=2")).scalar_one())
        assert set(erste["alarmstufen"]) == {"F3", "F14", "T4"}
        assert "min_alarmstufe" not in erste
        assert zweite == {"min_alarmstufe": 3}
