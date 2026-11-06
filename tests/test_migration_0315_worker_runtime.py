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


def test_worker_metriken_migration_0352_roundtrip():
    from app.models.worker_runtime import WorkerRuntime

    spec = importlib.util.spec_from_file_location("metriken_migration", "alembic/versions/0352_worker_runtime_metriken.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0352" and module.down_revision == "0351"
    assert WorkerRuntime.__table__.c.metriken.nullable
    assert isinstance(WorkerRuntime.__table__.c.metriken.type, sa.JSON)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE worker_runtime (worker_id VARCHAR(96) PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO worker_runtime VALUES ('vorher')"))
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        assert connection.execute(sa.text("SELECT metriken FROM worker_runtime")).scalar() is None
        column = next(c for c in sa.inspect(connection).get_columns("worker_runtime") if c["name"] == "metriken")
        assert column["nullable"]
        module.downgrade()
        assert [c["name"] for c in sa.inspect(connection).get_columns("worker_runtime")] == ["worker_id"]
    engine.dispose()
