"""Schema and data coverage for the GK SMS exercise migration."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.models  # noqa: F401
from alembic import op
from app.db import Base
from app.models.master import FireDept, OrgSettings


def _migration():
    path = Path(__file__).parents[1] / "alembic/versions/0278_gsl_gk_sms_uebung.py"
    spec = importlib.util.spec_from_file_location("migration_0278", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adds_gk_sms_uebung_and_copies_einsatzinfo_setting(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0278.db'}")
    Base.metadata.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            session = sa.orm.Session(bind=conn)
            settings = OrgSettings(
                org=FireDept(slug="gk-migration", name="GK", color="#123456", bos="Feuerwehr"),
                einsatzinfo_sms_send_exercise=True,
            )
            session.add(settings)
            session.flush()
            session.close()
            with op.batch_alter_table("org_settings") as batch:
                batch.drop_column("gk_sms_uebung")
            migration.upgrade()
            columns = {column["name"] for column in sa.inspect(conn).get_columns("org_settings")}
            assert "gk_sms_uebung" in columns
            assert conn.execute(sa.text("SELECT gk_sms_uebung FROM org_settings")).scalar() == 1
            migration.downgrade()
            columns = {column["name"] for column in sa.inspect(conn).get_columns("org_settings")}
            assert "gk_sms_uebung" not in columns
