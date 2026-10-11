"""Schema coverage for migration 0273."""
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
    path = Path(__file__).parents[1] / "alembic/versions/0273_mail_eingang_zuordnung.py"
    spec = importlib.util.spec_from_file_location("migration_0273", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_creates_mail_eingang_assignment_fields_and_downgrades(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0273.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    names = {"incident_id", "message_id_ref", "zugeordnet_at", "zuordnung", "zuordnung_grund"}
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            op.drop_index("ix_org_mail_eingang_incident_id", table_name="org_mail_eingang")
            with op.batch_alter_table("org_mail_eingang") as batch:
                for name in names:
                    batch.drop_column(name)
            migration.upgrade()
            assert names <= {column["name"] for column in sa.inspect(conn).get_columns("org_mail_eingang")}
            migration.downgrade()
            assert not names & {column["name"] for column in sa.inspect(conn).get_columns("org_mail_eingang")}
