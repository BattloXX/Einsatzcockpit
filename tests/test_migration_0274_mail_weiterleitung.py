"""Schema-Abdeckung für Migration 0274 (Weiterleitungen)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401
from alembic import op


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0274_mail_weiterleitung.py"
    spec = importlib.util.spec_from_file_location("migration_0274", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_creates_and_drops_weiterleitung_tables(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0274.db'}")
    migration = _migration()
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
        insp = sa.inspect(conn)
        assert {"mail_weiterleitung", "mail_weiterleitung_log"} <= set(insp.get_table_names())
        assert {c["name"] for c in insp.get_columns("mail_weiterleitung_log")} >= {
            "weiterleitung_id", "status", "versuche", "resend_email_id", "updated_at"}
        assert op.get_bind() is conn
        migration.downgrade()
        assert not {"mail_weiterleitung", "mail_weiterleitung_log"} & set(sa.inspect(conn).get_table_names())
