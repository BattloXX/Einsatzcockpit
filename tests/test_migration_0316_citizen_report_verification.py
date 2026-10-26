import importlib.util
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_citizen_report_verification_migration_roundtrip():
    path = Path("alembic/versions/0316_citizen_report_verification.py")
    spec = importlib.util.spec_from_file_location("citizen_verification_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0316"
    assert module.down_revision == "0315"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        parents = sa.MetaData()
        org = sa.Table("fire_dept", parents, sa.Column("id", sa.BigInteger(), primary_key=True))
        lage = sa.Table("major_incident", parents, sa.Column("id", sa.Integer(), primary_key=True))
        parents.create_all(connection)
        connection.execute(org.insert().values(id=1))
        connection.execute(lage.insert().values(id=1))
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        inspector = sa.inspect(connection)
        columns = {c["name"]: c for c in inspector.get_columns("citizen_report_verification")}
        assert set(columns) == {
            "token_hash", "org_id", "major_incident_id", "pin_hash", "phone", "form_data",
            "attempts", "expires_at", "consumed_at", "created_at",
        }
        assert columns["token_hash"]["type"].length == 64
        assert columns["pin_hash"]["type"].length == 64
        assert columns["token_hash"]["primary_key"]
        assert columns["consumed_at"]["nullable"]
        assert {fk["referred_table"] for fk in inspector.get_foreign_keys("citizen_report_verification")} == {
            "fire_dept", "major_incident",
        }
        assert any(i["column_names"] == ["expires_at"] for i in inspector.get_indexes("citizen_report_verification"))
        table = sa.Table("citizen_report_verification", sa.MetaData(), autoload_with=connection)
        connection.execute(table.insert().values(
            token_hash="a" * 64, org_id=1, major_incident_id=1, pin_hash="b" * 64,
            phone="+436641234567", form_data={"description": "Wasser", "photo_fn": "foto.jpg"},
            expires_at=datetime(2026, 10, 26), created_at=datetime(2026, 10, 26),
        ))
        row = connection.execute(sa.select(table)).one()
        assert row.attempts == 0 and row.consumed_at is None
        assert row.form_data["photo_fn"] == "foto.jpg"
        module.downgrade()
        assert "citizen_report_verification" not in sa.inspect(connection).get_table_names()
        module.upgrade()
    engine.dispose()
