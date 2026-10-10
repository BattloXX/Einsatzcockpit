"""Schema coverage for the GK phone-verification migration."""
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
    path = Path(__file__).parents[1] / "alembic/versions/0269_gsl_nummer_verifikation.py"
    spec = importlib.util.spec_from_file_location("migration_0269", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adds_and_removes_phone_verification_schema(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0269.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            op.drop_table("lage_einheit_nummer_verifikation")
            with op.batch_alter_table("lage_einheit_leader") as batch:
                batch.drop_column("phone_verifiziert_at")
            migration.upgrade()
            inspector = sa.inspect(conn)
            assert "phone_verifiziert_at" in {c["name"] for c in inspector.get_columns("lage_einheit_leader")}
            table = "lage_einheit_nummer_verifikation"
            assert {"phone_e164_neu", "code_hash", "gesperrt_bis", "zugang_id"} <= {
                c["name"] for c in inspector.get_columns(table)
            }
            assert {"ix_lenv_einheit", "ix_lenv_leader"} <= {i["name"] for i in inspector.get_indexes(table)}
            migration.downgrade()
            assert table not in sa.inspect(conn).get_table_names()
            columns = {c["name"] for c in sa.inspect(conn).get_columns("lage_einheit_leader")}
            assert "phone_verifiziert_at" not in columns
