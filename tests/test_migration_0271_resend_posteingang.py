"""Schema coverage for the Resend inbound mail migration."""
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
    path = Path(__file__).parents[1] / "alembic/versions/0271_resend_posteingang.py"
    spec = importlib.util.spec_from_file_location("migration_0271", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_creates_inbound_schema_and_downgrades(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0271.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            conn.execute(sa.text("DROP TABLE org_mail_eingang"))
            with op.batch_alter_table("org_resend_mail_config") as batch:
                batch.drop_column("inbound_retention_days")
                batch.drop_column("inbound_webhook_secret_enc")
                batch.drop_column("inbound_enabled")
            migration.upgrade()
            assert "org_mail_eingang" in sa.inspect(conn).get_table_names()
            columns = {item["name"] for item in sa.inspect(conn).get_columns("org_resend_mail_config")}
            assert {"inbound_enabled", "inbound_webhook_secret_enc", "inbound_retention_days"} <= columns
            migration.downgrade()
            assert "org_mail_eingang" not in sa.inspect(conn).get_table_names()
