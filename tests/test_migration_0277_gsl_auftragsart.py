"""Schema coverage for migration 0277."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401
from alembic import op
from app.db import Base


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0277_gsl_auftragsart.py"
    spec = importlib.util.spec_from_file_location("migration_0277", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adds_auftragsart_and_backfills_existing_dispatches(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0277.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            with op.batch_alter_table("einheit_site_dispatch") as batch:
                batch.drop_column("auftragsart")
            conn.execute(sa.text(
                "INSERT INTO einheit_site_dispatch (id, einheit_id, site_id, dispatched_at) "
                "VALUES (1, 1, 1, CURRENT_TIMESTAMP)"
            ))
            migration.upgrade()
        columns = {column["name"] for column in sa.inspect(conn).get_columns("einheit_site_dispatch")}
        assert "auftragsart" in columns
        wert = conn.execute(
            sa.text("SELECT auftragsart FROM einheit_site_dispatch WHERE id = 1")
        ).scalar()
        assert wert == "abarbeitung"


def test_downgrade_removes_auftragsart(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0277-down.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        columns = {column["name"] for column in sa.inspect(conn).get_columns("einheit_site_dispatch")}
        assert "auftragsart" not in columns
