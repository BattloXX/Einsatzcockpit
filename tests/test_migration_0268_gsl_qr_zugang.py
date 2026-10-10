"""Schema coverage for the GSL QR credential migration."""

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
    path = Path(__file__).parents[1] / "alembic/versions/0268_gsl_qr_zugang.py"
    spec = importlib.util.spec_from_file_location("migration_0268", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adds_qr_schema_and_downgrade_removes_qr_credentials(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0268.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            with op.batch_alter_table("lage_einheit_zugang") as batch:
                batch.drop_constraint("uq_lage_einheit_zugang_einheit_typ", type_="unique")
                batch.create_unique_constraint("uq_lage_einheit_zugang_einheit_id", ["einheit_id"])
                for column in ("qr_pin_pflicht", "qr_druck_job_id", "qr_druck_at", "typ"):
                    batch.drop_column(column)
            with op.batch_alter_table("lage_einheit_zugang_session") as batch:
                batch.drop_column("typ")
            with op.batch_alter_table("org_settings") as batch:
                for column in ("gk_qr_pin", "gk_qr_gueltigkeit_stunden", "gk_qr_aktiv"):
                    batch.drop_column(column)
            migration.upgrade()
            inspector = sa.inspect(conn)
            access_columns = {c["name"] for c in inspector.get_columns("lage_einheit_zugang")}
            assert {"typ", "qr_druck_at", "qr_druck_job_id", "qr_pin_pflicht"} <= access_columns
            assert "typ" in {c["name"] for c in inspector.get_columns("lage_einheit_zugang_session")}
            assert {"gk_qr_aktiv", "gk_qr_gueltigkeit_stunden", "gk_qr_pin"} <= {
                c["name"] for c in inspector.get_columns("org_settings")
            }
            conn.execute(
                sa.text(
                    "INSERT INTO lage_einheit_zugang "
                    "(id, einheit_id, lage_id, typ, phone_version, generation, status, einloesungen, "
                    "pin_pflicht, pin_versuche, qr_pin_pflicht, created_at, updated_at) "
                    "VALUES (999, 999, 999, 'qr', 1, 1, 'aktiv', 0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO lage_einheit_zugang_session "
                    "(id, zugang_id, generation, typ, session_hash, created_at, laeuft_ab_at) "
                    "VALUES (999, 999, 1, 'qr', 'session', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            migration.downgrade()
            assert "typ" not in {c["name"] for c in sa.inspect(conn).get_columns("lage_einheit_zugang")}
            session_count = conn.execute(
                sa.text("SELECT count(*) FROM lage_einheit_zugang_session WHERE zugang_id = 999")
            ).scalar()
            assert session_count == 0
            assert conn.execute(sa.text("SELECT count(*) FROM lage_einheit_zugang WHERE id = 999")).scalar() == 0
