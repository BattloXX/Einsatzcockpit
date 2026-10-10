"""Schema coverage for the GSL dispatch SMS settings migration."""

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
    path = Path(__file__).parents[1] / "alembic/versions/0270_gsl_auftrag_sms.py"
    spec = importlib.util.spec_from_file_location("migration_0270", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adds_dispatch_sms_settings_and_downgrades(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0270.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            with op.batch_alter_table("org_settings") as batch:
                batch.drop_column("gk_auftrag_nachricht")
                batch.drop_column("gk_auto_sms_auftrag")
            migration.upgrade()
            columns = {column["name"] for column in sa.inspect(conn).get_columns("org_settings")}
            assert {"gk_auto_sms_auftrag", "gk_auftrag_nachricht"} <= columns
            migration.downgrade()
            columns = {column["name"] for column in sa.inspect(conn).get_columns("org_settings")}
            assert "gk_auto_sms_auftrag" not in columns
            assert "gk_auftrag_nachricht" not in columns
