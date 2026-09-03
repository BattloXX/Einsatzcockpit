"""Regressionstest fuer die datenbasierte Member-Deduplizierung."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic/versions/0231_fahrtenbuch_member_dedup.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0231", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(modul)
    return modul


def test_member_dedup_haengt_fk_um_und_loest_unique_kollision(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0231.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE member (id INTEGER PRIMARY KEY, org_id INTEGER, sybos_id VARCHAR(50), "
            "lastname VARCHAR(100), firstname VARCHAR(100), phone VARCHAR(50), email VARCHAR(100), active BOOLEAN)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE member_qualification (member_id INTEGER, qualification_id INTEGER, "
            "PRIMARY KEY (member_id, qualification_id))"
        )
        conn.exec_driver_sql(
            "INSERT INTO member VALUES (1, NULL, NULL, 'Berger', 'Oliver', '123', 'alt@example.at', 1), "
            "(2, 1, 'S-2', 'berger', 'oliver', NULL, NULL, 1)"
        )
        conn.exec_driver_sql("INSERT INTO member_qualification VALUES (1, 10), (1, 11), (2, 10)")
        modul = _migration()
        with Operations.context(MigrationContext.configure(conn)):
            modul.upgrade()
            modul.upgrade()
        assert conn.execute(sa.text("SELECT id, phone, email FROM member")).all() == [(2, "123", "alt@example.at")]
        assert conn.execute(
            sa.text("SELECT member_id, qualification_id FROM member_qualification ORDER BY qualification_id")
        ).all() == [(2, 10), (2, 11)]
