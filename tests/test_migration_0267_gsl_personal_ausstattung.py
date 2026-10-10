"""Schema coverage for GSL personnel, equipment and association migration."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

import app.models  # noqa: F401
from alembic import op
from app.core.tenant import _TENANT_TABLE_NAMES
from app.db import Base


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0267_gsl_personal_ausstattung.py"
    spec = importlib.util.spec_from_file_location("migration_0267", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _remove_new_schema() -> None:
    op.drop_table("lage_einheit_ausstattung")
    op.drop_table("lage_einheit_person")
    op.drop_index("ix_lage_einheit_verband_id", table_name="lage_einheit")
    with op.batch_alter_table("lage_einheit") as batch:
        for column in (
            "aufgeteilt_von_id",
            "verband_id",
            "personal_bemerkung",
            "personal_modus",
            "staerke_sanitaeter",
            "staerke_agt",
            "staerke_fuehrung",
            "staerke_gesamt",
        ):
            batch.drop_column(column)


def test_adds_personal_ausstattung_schema_constraints_and_downgrades(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0267.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            _remove_new_schema()
            migration.upgrade()
            inspector = sa.inspect(conn)
            assert {"lage_einheit_person", "lage_einheit_ausstattung"} <= set(inspector.get_table_names())
            assert {
                "staerke_gesamt", "staerke_fuehrung", "staerke_agt", "staerke_sanitaeter",
                "personal_modus", "personal_bemerkung", "verband_id", "aufgeteilt_von_id",
            } <= {column["name"] for column in inspector.get_columns("lage_einheit")}
            assert {"lage_id", "einheit_id", "aktiv_key", "umbuchung_id"} <= {
                column["name"] for column in inspector.get_columns("lage_einheit_person")
            }
            assert {"kategorie", "ist_faehigkeit", "status", "updated_at"} <= {
                column["name"] for column in inspector.get_columns("lage_einheit_ausstattung")
            }
            assert "ix_lage_einheit_verband_id" in {
                index["name"] for index in inspector.get_indexes("lage_einheit")
            }

            conn.execute(sa.text(
                "INSERT INTO lage_einheit_person "
                "(id, org_id, lage_id, einheit_id, name, funktion, von_at, herkunft, aktiv_key, created_at) "
                "VALUES (1, NULL, 1, 1, 'Max Mustermann', 'mannschaft', :now, 'stamm', 42, :now)"
            ), {"now": now})
            with pytest.raises(IntegrityError):
                conn.execute(sa.text(
                    "INSERT INTO lage_einheit_person "
                    "(id, org_id, lage_id, einheit_id, name, funktion, von_at, herkunft, aktiv_key, created_at) "
                    "VALUES (2, NULL, 1, 2, 'Max Mustermann', 'mannschaft', :now, 'stamm', 42, :now)"
                ), {"now": now})
            conn.execute(sa.text(
                "INSERT INTO lage_einheit_person "
                "(id, org_id, lage_id, einheit_id, name, funktion, von_at, herkunft, aktiv_key, created_at) "
                "VALUES (3, NULL, 1, 1, 'Extern A', 'mannschaft', :now, 'frei', NULL, :now), "
                "(4, NULL, 1, 2, 'Extern B', 'mannschaft', :now, 'frei', NULL, :now)"
            ), {"now": now})
            migration.downgrade()

        inspector = sa.inspect(conn)
        assert "lage_einheit_person" not in inspector.get_table_names()
        assert "lage_einheit_ausstattung" not in inspector.get_table_names()
        assert "verband_id" not in {column["name"] for column in inspector.get_columns("lage_einheit")}


def test_personal_and_equipment_tables_are_tenant_scoped():
    assert {"lage_einheit_person", "lage_einheit_ausstattung"} <= _TENANT_TABLE_NAMES
