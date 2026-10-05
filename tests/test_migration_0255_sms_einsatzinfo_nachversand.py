"""Schema coverage for the persistent SMS retry incident reference."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401
from app.db import Base


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0255_sms_einsatzinfo_nachversand.py"
    spec = importlib.util.spec_from_file_location("migration_0255", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adds_sms_log_incident_reference(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0255.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        # Simulate the schema before this migration.
        with Operations.context(MigrationContext.configure(conn)):
            with op.batch_alter_table("sms_log") as batch:
                batch.drop_index("ix_sms_log_incident_id")
                batch.drop_column("incident_id")
            migration.upgrade()
        columns = {column["name"] for column in sa.inspect(conn).get_columns("sms_log")}
        assert "incident_id" in columns
        foreign_keys = sa.inspect(conn).get_foreign_keys("sms_log")
        assert any(fk["referred_table"] == "incident" for fk in foreign_keys)
