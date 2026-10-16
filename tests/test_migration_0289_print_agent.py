"""Portabilitaets- und Wiederaufsetzbarkeitstest fuer Migration 0289."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0289_print_agent.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0289", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _lauf(conn, funktion):
    with Operations.context(MigrationContext.configure(conn)):
        funktion()


def test_upgrade_downgrade_ist_wiederaufsetzbar(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0289.db'}")
    modul = _migration()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE fire_dept (id BIGINT PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE gateway (id BIGINT PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE printer (id BIGINT PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE print_job (id BIGINT PRIMARY KEY, status VARCHAR(20) NOT NULL)"
        )
        conn.exec_driver_sql("INSERT INTO gateway (id) VALUES (1)")
        conn.exec_driver_sql("INSERT INTO printer (id) VALUES (1)")
        conn.exec_driver_sql("INSERT INTO print_job (id, status) VALUES (1, 'queued')")

        _lauf(conn, modul.upgrade)
        _lauf(conn, modul.upgrade)

        gateway = conn.execute(sa.text("SELECT kind FROM gateway WHERE id=1")).scalar_one()
        job = conn.execute(
            sa.text("SELECT delivery, lease_generation FROM print_job WHERE id=1")
        ).one()
        assert gateway == "linux"
        assert job == ("push", 0)
        assert "printer_agent_route" in sa.inspect(conn).get_table_names()
        unique = sa.inspect(conn).get_unique_constraints("printer_agent_route")
        assert any(
            entry["name"] == "uq_printer_agent_route"
            and entry["column_names"] == ["printer_id", "gateway_id"]
            for entry in unique
        )

        _lauf(conn, modul.downgrade)
        _lauf(conn, modul.downgrade)
        assert "printer_agent_route" not in sa.inspect(conn).get_table_names()
        assert "kind" not in {spalte["name"] for spalte in sa.inspect(conn).get_columns("gateway")}
