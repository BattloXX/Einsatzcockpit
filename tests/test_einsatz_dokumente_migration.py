"""Additive Migration und Binärtyp für unabhängige Einsatzfassungen."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import mysql

from app.db import Base
from app.models.einsatz_dokument import EinsatzDokumentFassung


def test_migration_altbestand_upgrade_downgrade_und_mysql_blob(tmp_path):
    path = Path(__file__).parents[1] / "alembic/versions/0309_einsatz_dokumente.py"
    spec = importlib.util.spec_from_file_location("migration_0309", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0309.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
            conn.execute(Base.metadata.tables["fire_dept"].insert().values(
                id=1, slug="migration", name="Bestand", color="#123456"))
            conn.execute(Base.metadata.tables["incident"].insert().values(
                id=1, primary_org_id=1, status="closed", alarm_type_code="B1"))
            migration.upgrade()
            assert conn.execute(sa.text("SELECT status FROM incident WHERE id=1")).scalar() == "closed"
            for table in ("einsatz_dokument", "einsatz_dokument_fassung", "einsatz_dokument_empfaenger"):
                assert conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0
            assert len(sa.inspect(conn).get_foreign_keys("einsatz_dokument")) == 7
            migration.downgrade()
            assert conn.execute(sa.text("SELECT alarm_type_code FROM incident WHERE id=1")).scalar() == "B1"
            migration.upgrade()
    column = EinsatzDokumentFassung.__table__.c.inhalt
    assert column.type.compile(dialect=mysql.dialect()) == "LONGBLOB"
