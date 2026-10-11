"""Schema-Abdeckung fuer Migration 0275 (Material an Einsatzstellen)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0275_verleih_material_einsatzstelle.py"
    spec = importlib.util.spec_from_file_location("migration_0275", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adds_and_removes_material_columns(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0275.db'}")
    metadata = sa.MetaData()
    sa.Table("lage_einheit", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("verleih_ausleihe", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("verleih_foto", metadata, sa.Column("id", sa.Integer, primary_key=True))
    metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
        inspector = sa.inspect(conn)
        assert {"quelle", "einheit_id", "created_by_name"} <= {
            column["name"] for column in inspector.get_columns("verleih_ausleihe")
        }
        assert "author_name" in {column["name"] for column in inspector.get_columns("verleih_foto")}
        assert "ix_verleih_ausleihe_einheit_id" in {
            index["name"] for index in inspector.get_indexes("verleih_ausleihe")
        }
        migration.downgrade()
        assert not {"quelle", "einheit_id", "created_by_name"} & {
            column["name"] for column in sa.inspect(conn).get_columns("verleih_ausleihe")
        }
