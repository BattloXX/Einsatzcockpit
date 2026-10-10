"""Schema coverage for GSL-Ressourcenkarte Phase 1."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401
from alembic import op
from app.db import Base


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0265_gsl_ressourcenkarte.py"
    spec = importlib.util.spec_from_file_location("migration_0265", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _remove_new_columns() -> None:
    op.drop_index("ix_lage_journal_entry_einheit_id", table_name="lage_journal_entry")
    with op.batch_alter_table("lage_journal_entry") as batch:
        for column in (
            "storno_grund", "storniert_von", "storniert_at", "quelle",
            "ereignis_typ", "site_id", "einheit_id",
        ):
            batch.drop_column(column)
    with op.batch_alter_table("einheit_site_dispatch") as batch:
        for column in ("withdrawn_grund", "withdrawn_author", "withdrawn_by"):
            batch.drop_column(column)
    op.drop_column("vehicle_master", "funkrufname")
    with op.batch_alter_table("lage_einheit") as batch:
        for column in ("status_at", "bereitstellungsraum", "funkrufname"):
            batch.drop_column(column)
    op.drop_index("ix_lel_einheit_aktiv", table_name="lage_einheit_leader")
    with op.batch_alter_table("lage_einheit_leader") as batch:
        for column in ("ende_von", "ende_grund", "phone_version", "phone_e164", "phone", "rolle"):
            batch.drop_column(column)


def test_adds_ressourcenkarte_schema_backfills_status_and_downgrades(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0265.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    added_at = datetime(2026, 10, 9, 8, tzinfo=UTC)
    committed_at = added_at + timedelta(hours=2)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            _remove_new_columns()
            conn.execute(sa.text(
                "INSERT INTO lage_einheit "
                "(id, lage_id, label, status, is_from_org, resource_type, added_at, committed_at) "
                "VALUES (1, 1, 'RLF 1', 'im_einsatz', 0, 'fahrzeug', :added_at, :committed_at)"
            ), {"added_at": added_at, "committed_at": committed_at})
            migration.upgrade()

            inspector = sa.inspect(conn)
            assert {"rolle", "phone", "phone_e164", "phone_version", "ende_grund", "ende_von"} <= {
                c["name"] for c in inspector.get_columns("lage_einheit_leader")
            }
            assert {"funkrufname", "bereitstellungsraum", "status_at"} <= {
                c["name"] for c in inspector.get_columns("lage_einheit")
            }
            assert {"withdrawn_by", "withdrawn_author", "withdrawn_grund"} <= {
                c["name"] for c in inspector.get_columns("einheit_site_dispatch")
            }
            assert {
                "einheit_id", "site_id", "ereignis_typ", "quelle", "storniert_at",
                "storniert_von", "storno_grund",
            } <= {
                c["name"] for c in inspector.get_columns("lage_journal_entry")
            }
            status_at = conn.execute(
                sa.text("SELECT status_at FROM lage_einheit WHERE id = 1")
            ).scalar_one()
            assert str(status_at).startswith("2026-10-09 10:00:00")
            migration.downgrade()

        inspector = sa.inspect(conn)
        assert "status_at" not in {c["name"] for c in inspector.get_columns("lage_einheit")}
        assert "funkrufname" not in {c["name"] for c in inspector.get_columns("vehicle_master")}
