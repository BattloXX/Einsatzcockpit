"""Reversibles Schema, FK-Typen und Unique-Constraints auf SQLite/MariaDB."""

from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.tenant import TenantContextMissing, TenantScoped
from app.db import Base
from app.models.infoscreen_agent import InfoscreenRelease
from tests.test_migration_0332_objekt_dokument_georef import migration

TABLES = sorted(t for t in Base.metadata.tables if t.startswith("infoscreen_") and t != "infoscreen_url")


def test_upgrade_downgrade_modell(tmp_path):
    neu = migration("0355", "infoscreen_agent")
    assert neu.revision == "0355" and neu.down_revision == "0354"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'infoscreen.db'}")
    with engine.begin() as conn:
        # Externe FK-Ziele in exakt denselben Typen wie im Bestand.
        for name in ("fire_dept", "user", "alarm_infoscreen_token"):
            conn.exec_driver_sql(f'CREATE TABLE "{name}" (id BIGINT PRIMARY KEY)')
        conn.exec_driver_sql("CREATE TABLE org_settings (id INTEGER PRIMARY KEY, org_id BIGINT)")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                neu.upgrade()
                migration("0356", "infoscreen_resolution_hash").upgrade()
                migration("0357", "infoscreen_heartbeat").upgrade()
                migration("0358", "infoscreen_releases").upgrade()
            inspector = sa.inspect(conn)
            assert len(TABLES) == 10
            for name in TABLES:
                table = Base.metadata.tables[name]
                columns = {c["name"]: c for c in inspector.get_columns(name)}
                assert set(columns) == set(table.columns.keys())
                for c in table.columns:
                    assert columns[c.name]["nullable"] == c.nullable
                    assert columns[c.name]["type"]._type_affinity == c.type._type_affinity
                for index in table.indexes:
                    actual = next(i for i in inspector.get_indexes(name) if i["name"] == index.name)
                    assert actual["column_names"] == [c.name for c in index.columns]
                    assert bool(actual["unique"]) == bool(index.unique)
                actual_unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)}
                for constraint in table.constraints:
                    if isinstance(constraint, sa.UniqueConstraint):
                        assert tuple(c.name for c in constraint.columns) in actual_unique
                actual_fks = inspector.get_foreign_keys(name)
                for fk in table.foreign_keys:
                    actual = next(f for f in actual_fks if f["constrained_columns"] == [fk.parent.name])
                    assert actual["referred_table"] == fk.column.table.name
                    assert actual["options"]["ondelete"] == fk.ondelete
            conn.exec_driver_sql("INSERT INTO fire_dept VALUES (1)")
            conn.exec_driver_sql("INSERT INTO alarm_infoscreen_token VALUES (1)")
            conn.exec_driver_sql(
                "INSERT INTO infoscreen_device (id, org_id, name, erstellt_am) VALUES (1, 1, 'Test', CURRENT_TIMESTAMP)"
            )
            for key in ("main", "second"):
                if key == "second":
                    with pytest.raises(sa.exc.IntegrityError):
                        conn.execute(
                            sa.text(
                                "INSERT INTO infoscreen_display (org_id, device_id, display_key, name, alarm_token_id) "
                                "VALUES (1, 1, :key, :key, 1)"
                            ),
                            {"key": key},
                        )
                else:
                    conn.execute(
                        sa.text(
                            "INSERT INTO infoscreen_display (org_id, device_id, display_key, name, alarm_token_id) "
                            "VALUES (1, 1, :key, :key, 1)"
                        ),
                        {"key": key},
                    )
            with Operations.context(MigrationContext.configure(conn)):
                migration("0358", "infoscreen_releases").downgrade()
                migration("0357", "infoscreen_heartbeat").downgrade()
                migration("0356", "infoscreen_resolution_hash").downgrade()
                neu.downgrade()
            assert not set(TABLES) & set(sa.inspect(conn).get_table_names())
            conn.exec_driver_sql("DELETE FROM alarm_infoscreen_token")
            conn.exec_driver_sql("DELETE FROM fire_dept")
    engine.dispose()


@pytest.mark.parametrize("dialekt", ["sqlite", "mysql", "mariadb"])
def test_ddl_und_fk_typen(dialekt):
    output = StringIO()
    with Operations.context(
        MigrationContext.configure(
            dialect_name=dialekt,
            opts={"as_sql": True, "output_buffer": output},
        )
    ):
        neu = migration("0355", "infoscreen_agent")
        neu.upgrade()
        neu.downgrade()
    sql = output.getvalue()
    integer_type = "INTEGER" if dialekt == "sqlite" else "BIGINT"
    assert f"alarm_token_id {integer_type}" in sql
    assert "FOREIGN KEY(alarm_token_id) REFERENCES alarm_infoscreen_token (id) ON DELETE SET NULL" in sql
    assert "UNIQUE (alarm_token_id)" in sql
    assert f"revision {integer_type}" in sql
    for name in TABLES:
        for fk in Base.metadata.tables[name].foreign_keys:
            dialect = sa.dialects.mysql.dialect()
            assert fk.parent.type.compile(dialect=dialect) == fk.column.type.compile(dialect=dialect)


def test_tenant_modelle_fail_closed():
    from tests.conftest import TestingSession

    assert not issubclass(InfoscreenRelease, TenantScoped)
    with TestingSession() as db:
        for mapper in Base.registry.mappers:
            if mapper.local_table.name in set(TABLES) - {"infoscreen_release", "infoscreen_release_upload_token"}:
                assert issubclass(mapper.class_, TenantScoped)
                with pytest.raises(TenantContextMissing):
                    db.query(mapper.class_).first()
