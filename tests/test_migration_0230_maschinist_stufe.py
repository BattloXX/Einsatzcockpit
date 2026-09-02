"""Wiederaufsetzbarkeit der Maschinisten-Stufen-Migration."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic/versions/0230_qualification_maschinist_stufe.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0230", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(modul)
    return modul


def _lauf(conn, funktion):
    with Operations.context(MigrationContext.configure(conn)):
        funktion()


def test_upgrade_downgrade_ist_wiederaufsetzbar(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0230.db'}")
    modul = _migration()
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE qualification (id INTEGER PRIMARY KEY, code VARCHAR(30) UNIQUE, "
            "label VARCHAR(100), is_einsatzleiter BOOLEAN DEFAULT 0, "
            "is_gruppenkommandant BOOLEAN DEFAULT 0)"
        )
        conn.exec_driver_sql("INSERT INTO qualification (code, label) VALUES ('M1', 'Bestehendes Label')")
        _lauf(conn, modul.upgrade)
        _lauf(conn, modul.upgrade)
        assert "maschinist_stufe" in {s["name"] for s in sa.inspect(conn).get_columns("qualification")}
        rows = conn.execute(sa.text(
            "SELECT code, label, maschinist_stufe FROM qualification "
            "WHERE code LIKE 'M_' ORDER BY code"
        )).all()
        assert len(rows) == 4
        assert rows[0] == ("M1", "Bestehendes Label", 1)
        _lauf(conn, modul.downgrade)
        _lauf(conn, modul.downgrade)
        assert "maschinist_stufe" not in {s["name"] for s in sa.inspect(conn).get_columns("qualification")}
        _lauf(conn, modul.upgrade)
        assert len(conn.execute(sa.text("SELECT id FROM qualification WHERE code LIKE 'M_' ")).all()) == 4
