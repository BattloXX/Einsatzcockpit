"""Schema coverage for migration 0272."""
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
    path = Path(__file__).parents[1] / "alembic/versions/0272_mail_eingang_einstellungen.py"
    spec = importlib.util.spec_from_file_location("migration_0272", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_creates_mail_eingang_settings_and_downgrades(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0272.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    names = {
        "mail_eingang_modus", "mail_eingang_rueckblick_min", "mail_eingang_mehrere",
        "mail_eingang_absender", "mail_eingang_max_pro_stunde",
    }
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            with op.batch_alter_table("org_settings") as batch:
                for name in names:
                    batch.drop_column(name)
            migration.upgrade()
            assert names <= {column["name"] for column in sa.inspect(conn).get_columns("org_settings")}
            migration.downgrade()
            assert not names & {column["name"] for column in sa.inspect(conn).get_columns("org_settings")}
