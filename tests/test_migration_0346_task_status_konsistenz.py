"""Konsistenzreparatur fuer alle Organisationen, ohne Verlust weiterer Auftragsdaten."""
import importlib.util
from io import StringIO
from itertools import product
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0346_task_status_konsistenz.py"
    spec = importlib.util.spec_from_file_location("migration_0346", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reparatur_aller_flags_kombinationen_idempotent_und_downgrade_noop():
    module = migration()
    assert module.revision == "0346" and module.down_revision == "0345"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE task (id INTEGER PRIMARY KEY, incident_id INTEGER, "
                             "status TEXT, is_done BOOLEAN, is_cancelled BOOLEAN, detail TEXT)")
        zeilen = []
        erwartet = []
        for ident, (incident, status, done, cancelled) in enumerate(product(
                (1, 2), ("open", "in_progress", "done", "cancelled", "unbekannt"), (0, 1), (0, 1)), 1):
            zeilen.append({"id": ident, "incident": incident, "status": status,
                           "done": done, "cancelled": cancelled})
            if status in ("open", "in_progress"):
                if cancelled:
                    status, done = "cancelled", 0
                elif done:
                    status = "done"
            if status == "done":
                done = 1
            elif status == "cancelled":
                cancelled = 1
            erwartet.append((ident, incident, status, done, cancelled, "Original"))
        conn.execute(sa.text("INSERT INTO task VALUES (:id, :incident, :status, :done, :cancelled, 'Original')"),
                     zeilen)
        for funktion in (module.upgrade, module.upgrade, module.downgrade):
            with Operations.context(MigrationContext.configure(conn)):
                funktion()
            assert conn.exec_driver_sql("SELECT * FROM task ORDER BY id").all() == erwartet
    engine.dispose()


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "mariadb"])
def test_plain_sql_fuer_beide_datenbanken(dialect):
    output = StringIO()
    context = MigrationContext.configure(dialect_name=dialect, opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration().upgrade()
        migration().downgrade()
    sql = output.getvalue()
    assert sql.count("UPDATE task SET") == 4
    assert "is_done = 0" in sql and "is_cancelled = 1" in sql
