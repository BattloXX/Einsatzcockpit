import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_worker_runtime_migration_roundtrip():
    path = Path("alembic/versions/0315_worker_runtime.py")
    spec = importlib.util.spec_from_file_location("runtime_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        assert "worker_runtime" in sa.inspect(connection).get_table_names()
        columns = {c["name"] for c in sa.inspect(connection).get_columns("worker_runtime")}
        assert {"worker_id", "expires_at", "loops", "redis", "is_leader"} <= columns
        module.downgrade()
        assert "worker_runtime" not in sa.inspect(connection).get_table_names()
        module.upgrade()
    engine.dispose()
