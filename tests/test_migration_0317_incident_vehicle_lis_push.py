import importlib.util
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_incident_vehicle_lis_push_migration_roundtrip():
    path = Path("alembic/versions/0317_incident_vehicle_lis_push.py")
    spec = importlib.util.spec_from_file_location("lis_push_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0317"
    assert module.down_revision == "0316"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE incident_vehicle (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO incident_vehicle (id) VALUES (1)")
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        columns = {c["name"]: c for c in sa.inspect(connection).get_columns("incident_vehicle")}
        assert set(columns) == {"id", "lis_pushed_status", "lis_pushed_at"}
        assert columns["lis_pushed_status"]["type"].length == 16
        assert isinstance(columns["lis_pushed_at"]["type"], sa.DateTime)
        assert columns["lis_pushed_status"]["nullable"]
        assert columns["lis_pushed_at"]["nullable"]
        table = sa.Table("incident_vehicle", sa.MetaData(), autoload_with=connection)
        row = connection.execute(sa.select(table)).one()
        assert row.id == 1 and row.lis_pushed_status is None and row.lis_pushed_at is None
        now = datetime(2026, 10, 26, 12, 0)
        connection.execute(table.update().where(table.c.id == 1).values(lis_pushed_status="S5", lis_pushed_at=now))
        row = connection.execute(sa.select(table)).one()
        assert row.lis_pushed_status == "S5" and row.lis_pushed_at == now
        assert row.lis_pushed_at.tzinfo is None
        module.downgrade()
        assert {c["name"] for c in sa.inspect(connection).get_columns("incident_vehicle")} == {"id"}
        assert connection.exec_driver_sql("SELECT id FROM incident_vehicle").scalar_one() == 1
        module.upgrade()
        row = connection.execute(sa.select(table)).one()
        assert row.lis_pushed_status is None and row.lis_pushed_at is None
    engine.dispose()
