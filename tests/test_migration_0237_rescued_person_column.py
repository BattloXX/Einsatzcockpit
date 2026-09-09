"""Backfill coverage for migration 0237."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401
from app.db import Base


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0237_rescued_person_column.py"
    spec = importlib.util.spec_from_file_location("migration_0237", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_backfills_people_to_the_first_rescued_column_and_enforces_fk(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0237.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()

    with engine.begin() as conn:
        conn.execute(sa.insert(Base.metadata.tables["incident"]).values(id=901, alarm_type_code="T1"))
        conn.execute(sa.insert(Base.metadata.tables["incident_column"]), [
            {"id": 101, "incident_id": 901, "code": "rescued_late", "title": "Späte Personen",
             "column_kind": "rescued", "is_fixed": False, "display_order": 20},
            {"id": 102, "incident_id": 901, "code": "rescued_first", "title": "Frühe Personen",
             "column_kind": "rescued", "is_fixed": False, "display_order": 10},
        ])
        # Simulate the pre-0237 table while retaining the genuine incident and
        # incident_column tables of the prior schema.
        conn.execute(sa.text("DROP TABLE rescued_person"))
        conn.execute(sa.text("""
            CREATE TABLE rescued_person (
                id BIGINT PRIMARY KEY, incident_id BIGINT NOT NULL,
                gender VARCHAR(30) NOT NULL, person_group VARCHAR(30) NOT NULL,
                age_range VARCHAR(30), name VARCHAR(150), location VARCHAR(300),
                vehicle_id BIGINT, status VARCHAR(20) NOT NULL, created_at DATETIME
            )
        """))
        conn.execute(sa.text("""
            INSERT INTO rescued_person
                (id, incident_id, gender, person_group, name, status, created_at)
            VALUES (1, 901, 'Unbekannt', 'Erwachsen', 'Bestand', 'gefunden', '2026-01-01')
        """))

        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()

        assert conn.execute(sa.text("SELECT column_id FROM rescued_person WHERE id=1")).scalar_one() == 102
        assert conn.execute(sa.text("SELECT count(*) FROM rescued_person WHERE column_id IS NULL")).scalar_one() == 0
        foreign_keys = sa.inspect(conn).get_foreign_keys("rescued_person")
        assert any(fk["referred_table"] == "incident_column" and fk["constrained_columns"] == ["column_id"]
                   for fk in foreign_keys)

        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "column_id" not in {column["name"] for column in sa.inspect(conn).get_columns("rescued_person")}
