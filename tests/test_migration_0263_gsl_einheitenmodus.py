"""Schema coverage for GSL-Einheitenmodus-Auftragsdaten und Geräteprotokoll."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401
from alembic import op
from app.core.tenant import _TENANT_TABLE_NAMES
from app.db import Base
from app.models.major_incident import (
    EINHEIT_STATUS_LABEL,
    EINHEIT_STATUS_WERTE,
    SITE_LOG_KIND_LABEL,
    SITE_LOG_USER_KINDS,
)


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0263_gsl_einheitenmodus.py"
    spec = importlib.util.spec_from_file_location("migration_0263", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adds_gsl_einheitenmodus_schema_and_backfills_dispatches(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0263.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    vor_ort_at = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)
    dispatched_at = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    with engine.begin() as conn:
        # Simulate the schema before this migration.
        with Operations.context(MigrationContext.configure(conn)):
            op.drop_index("ix_esd_einheit_aktiv", table_name="einheit_site_dispatch")
            with op.batch_alter_table("einheit_site_dispatch") as batch:
                batch.drop_column("letzte_rueckmeldung_at")
                batch.drop_column("geaendert_at")
                batch.drop_column("version")
                batch.drop_column("reihenfolge")
                batch.drop_column("beendet_grund")
                batch.drop_column("beendet_at")
                batch.drop_column("bestaetigt_at")
                batch.drop_column("status_at")
                batch.drop_column("einheit_status")
                batch.drop_column("auftrag")
            op.drop_index("ix_site_log_entry_einheit_id", table_name="site_log_entry")
            with op.batch_alter_table("site_log_entry") as batch:
                batch.drop_column("erfasst_at")
                batch.drop_column("einheit_id")
            op.drop_index("ix_site_media_einheit_id", table_name="site_media")
            with op.batch_alter_table("site_media") as batch:
                batch.drop_column("erfasst_at")
                batch.drop_column("kommentar")
                batch.drop_column("einheit_id")
            op.drop_column("device_token", "gsl_profil")
            op.drop_index("ix_einheit_aktion_org_id", table_name="einheit_aktion")
            op.drop_index("ix_einheit_aktion_einheit_id", table_name="einheit_aktion")
            op.drop_table("einheit_aktion")

            conn.execute(
                sa.text(
                    "INSERT INTO einheit_site_dispatch "
                    "(id, einheit_id, site_id, dispatched_at, vor_ort_at) "
                    "VALUES (1, 1, 1, :dispatched_at, :vor_ort_at), "
                    "(2, 2, 2, :dispatched_at, NULL)"
                ),
                {"dispatched_at": dispatched_at, "vor_ort_at": vor_ort_at},
            )
            migration.upgrade()

        inspector = sa.inspect(conn)
        dispatch_columns = {column["name"] for column in inspector.get_columns("einheit_site_dispatch")}
        assert {
            "auftrag", "einheit_status", "status_at", "bestaetigt_at", "beendet_at",
            "beendet_grund", "reihenfolge", "version", "geaendert_at", "letzte_rueckmeldung_at",
        } <= dispatch_columns
        assert inspector.has_table("einheit_aktion")
        assert {"einheit_id", "erfasst_at"} <= {
            column["name"] for column in inspector.get_columns("site_log_entry")
        }
        assert {"einheit_id", "kommentar", "erfasst_at"} <= {
            column["name"] for column in inspector.get_columns("site_media")
        }
        assert "gsl_profil" in {column["name"] for column in inspector.get_columns("device_token")}
        assert {"ix_einheit_aktion_einheit_id", "ix_einheit_aktion_org_id"} <= {
            index["name"] for index in inspector.get_indexes("einheit_aktion")
        }
        rows = conn.execute(
            sa.text(
                "SELECT id, einheit_status, status_at, version "
                "FROM einheit_site_dispatch ORDER BY id"
            )
        ).mappings().all()
        assert rows[0]["einheit_status"] == "vor_ort"
        assert rows[0]["status_at"] is not None
        assert rows[1]["einheit_status"] == "zugewiesen"
        assert rows[1]["status_at"] is not None
        assert [row["version"] for row in rows] == [1, 1]


def test_gsl_einheitenmodus_model_constants_are_complete():
    assert "einheit_aktion" in _TENANT_TABLE_NAMES
    assert set(EINHEIT_STATUS_LABEL) == set(EINHEIT_STATUS_WERTE)
    assert SITE_LOG_KIND_LABEL["einheit"] == "Einheit"
    assert "einheit" not in SITE_LOG_USER_KINDS
