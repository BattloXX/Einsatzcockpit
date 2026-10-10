"""Schema coverage for the hash-only Gruppenkommandanten access migration."""

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
    path = Path(__file__).parents[1] / "alembic/versions/0266_gsl_gk_zugang.py"
    spec = importlib.util.spec_from_file_location("migration_0266", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adds_gk_access_schema_and_downgrades(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0266.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            with op.batch_alter_table("einheit_aktion") as batch:
                batch.drop_column("zugang_id")
            op.drop_table("lage_einheit_zugang_versand")
            op.drop_table("lage_einheit_zugang_session")
            op.drop_table("lage_einheit_zugang")
            with op.batch_alter_table("org_settings") as batch:
                for column in (
                    "gk_zugang_ressource_pflegen",
                    "gk_zugang_sms_pin",
                    "gk_zugang_max_sitzungen",
                    "gk_sitzung_stunden",
                    "gk_zugang_gueltigkeit_stunden",
                    "gk_zugang_nachricht",
                    "gk_zugang_auto_sms",
                    "gk_zugang_aktiv",
                ):
                    batch.drop_column(column)
            migration.upgrade()
            inspector = sa.inspect(conn)
            assert {"lage_einheit_zugang", "lage_einheit_zugang_session", "lage_einheit_zugang_versand"} <= set(
                inspector.get_table_names()
            )
            assert "zugang_id" in {c["name"] for c in inspector.get_columns("einheit_aktion")}
            assert {"gk_zugang_aktiv", "gk_zugang_ressource_pflegen"} <= {
                c["name"] for c in inspector.get_columns("org_settings")
            }
            assert any(
                c["name"] == "token_hash" and c["type"].length == 64
                for c in inspector.get_columns("lage_einheit_zugang")
            )
            migration.downgrade()
        inspector = sa.inspect(conn)
        assert "lage_einheit_zugang" not in inspector.get_table_names()
        assert "zugang_id" not in {c["name"] for c in inspector.get_columns("einheit_aktion")}
