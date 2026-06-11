"""Multi-tenancy PR 2 (Contract): NOT NULL + FKs, alte alarm_type_code Spalten droppen

Revision ID: 0047
Revises: 0046
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def _drop_all_fks_referencing(conn, referenced_table):
    """Drop alle FK-Constraints, die aus irgendwelchen Tabellen auf 'referenced_table' zeigen.

    Nötig bevor ein ALTER TABLE auf der referenzierten Tabelle ausgeführt wird –
    MariaDB erzeugt sonst errno 150 beim Rename des Temp-Tableaus.
    """
    r = conn.execute(text("""
        SELECT TABLE_NAME, CONSTRAINT_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE()
          AND REFERENCED_TABLE_NAME = :t
    """), {"t": referenced_table})
    for row in r.fetchall():
        try:
            conn.execute(text(f"ALTER TABLE `{row[0]}` DROP FOREIGN KEY `{row[1]}`"))
        except Exception:
            pass  # Bereits entfernt oder kein DROP-Recht nötig


def _constraint_exists(conn, table, constraint_name):
    r = conn.execute(text("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :t
          AND CONSTRAINT_NAME = :c
    """), {"t": table, "c": constraint_name})
    return r.scalar() > 0


def _column_exists(conn, table, column):
    r = conn.execute(text("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c
    """), {"t": table, "c": column})
    return r.scalar() > 0


def upgrade():
    conn = op.get_bind()

    # 1. Alle FK-Constraints, die von irgendwo auf alarm_type zeigen, entfernen.
    #    Ohne diesen Schritt schlägt das ALTER TABLE unten mit errno 150 fehl,
    #    weil MariaDB beim Rename der Temp-Tabelle bestehende Referenzen validiert.
    _drop_all_fks_referencing(conn, "alarm_type")

    # 2. alarm_type.org_id NOT NULL + FK zu fire_dept + Unique-Constraint
    #    Idempotent: FK und UNIQUE nur anlegen wenn noch nicht vorhanden
    conn.execute(text(
        "ALTER TABLE `alarm_type` MODIFY COLUMN `org_id` BIGINT NOT NULL"
    ))
    if not _constraint_exists(conn, "alarm_type", "fk_alarm_type_org_id"):
        conn.execute(text(
            "ALTER TABLE `alarm_type`"
            "  ADD CONSTRAINT `fk_alarm_type_org_id`"
            "    FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE CASCADE"
        ))
    if not _constraint_exists(conn, "alarm_type", "uq_alarm_type_org_code"):
        conn.execute(text(
            "ALTER TABLE `alarm_type`"
            "  ADD CONSTRAINT `uq_alarm_type_org_code` UNIQUE (`org_id`, `code`)"
        ))

    # 3. Jede Junction-Tabelle: alarm_type_id NOT NULL, FK hinzufügen, alarm_type_code droppen
    for table in [
        "task_suggestion_alarm",
        "message_suggestion_alarm",
        "lage_hint_alarm",
        "default_message_alarm",
        "alarm_dispatch_vehicle",
    ]:
        # MODIFY nur wenn Spalte existiert; FK + DROP nur wenn noch nicht done
        if not _column_exists(conn, table, "alarm_type_id"):
            continue  # Tabelle fehlt oder Spalte noch nicht da – überspringen
        conn.execute(text(
            f"ALTER TABLE `{table}` MODIFY COLUMN `alarm_type_id` BIGINT NOT NULL"
        ))
        if not _constraint_exists(conn, table, f"fk_{table}_alarm_type"):
            conn.execute(text(
                f"ALTER TABLE `{table}`"
                f"  ADD CONSTRAINT `fk_{table}_alarm_type`"
                f"    FOREIGN KEY (`alarm_type_id`) REFERENCES `alarm_type` (`id`) ON DELETE CASCADE"
            ))
        if _column_exists(conn, table, "alarm_type_code"):
            conn.execute(text(f"ALTER TABLE `{table}` DROP COLUMN `alarm_type_code`"))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0047")
