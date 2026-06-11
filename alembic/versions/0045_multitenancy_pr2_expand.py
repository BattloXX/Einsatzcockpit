"""Multi-tenancy PR 2 (Expand): AlarmType surrogate PK + org_id, alarm_type_id auf FK-Tabellen

Revision ID: 0045
Revises: 0044
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def _drop_fks_on_column(conn, table, column):
    """Drop alle FK-Constraints, die auf 'column' in 'table' zeigen."""
    r = conn.execute(text(
        "SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        " AND REFERENCED_TABLE_NAME IS NOT NULL"
    ), {"t": table, "c": column})
    for row in r:
        conn.execute(text(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{row[0]}`"))


def upgrade():
    conn = op.get_bind()

    # 1. FK-Constraints von den 5 Junction-Tabellen auf alarm_type.code entfernen
    for table in [
        "task_suggestion_alarm",
        "message_suggestion_alarm",
        "lage_hint_alarm",
        "default_message_alarm",
        "alarm_dispatch_vehicle",
    ]:
        _drop_fks_on_column(conn, table, "alarm_type_code")

    # 2. alarm_dispatch_vehicle: alten Unique-Index auf alarm_type_code entfernen (falls vorhanden)
    try:
        conn.execute(text("ALTER TABLE `alarm_dispatch_vehicle` DROP INDEX `uq_alarm_vehicle`"))
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE `alarm_dispatch_vehicle` DROP INDEX `ix_alarm_dispatch_alarm_type`"))
    except Exception:
        pass

    # 3. alarm_type: PK von code auf id (BigInt AUTO_INCREMENT) umstellen + org_id hinzufügen
    conn.execute(text("""
        ALTER TABLE `alarm_type`
          DROP PRIMARY KEY,
          ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT FIRST,
          ADD PRIMARY KEY (`id`),
          ADD COLUMN `org_id` INT NULL DEFAULT NULL,
          ADD INDEX `ix_alarm_type_org_id` (`org_id`)
    """))

    # 4. alarm_type_id (nullable) auf alle 5 Junction-Tabellen hinzufügen
    for table in [
        "task_suggestion_alarm",
        "message_suggestion_alarm",
        "lage_hint_alarm",
        "default_message_alarm",
        "alarm_dispatch_vehicle",
    ]:
        conn.execute(text(
            f"ALTER TABLE `{table}` ADD COLUMN `alarm_type_id` BIGINT NULL DEFAULT NULL"
        ))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0045")
