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


def upgrade():
    conn = op.get_bind()

    # 1. alarm_type.org_id NOT NULL machen + FK + Unique-Constraint
    conn.execute(text("""
        ALTER TABLE `alarm_type`
          MODIFY COLUMN `org_id` INT NOT NULL,
          ADD CONSTRAINT `fk_alarm_type_org_id`
            FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE CASCADE,
          ADD CONSTRAINT `uq_alarm_type_org_code` UNIQUE (`org_id`, `code`)
    """))

    # 2. Jede Junction-Tabelle: alarm_type_id NOT NULL, FK hinzufügen, alarm_type_code droppen
    for table in [
        "task_suggestion_alarm",
        "message_suggestion_alarm",
        "lage_hint_alarm",
        "default_message_alarm",
        "alarm_dispatch_vehicle",
    ]:
        conn.execute(text(f"""
            ALTER TABLE `{table}`
              MODIFY COLUMN `alarm_type_id` BIGINT NOT NULL,
              ADD CONSTRAINT `fk_{table}_alarm_type`
                FOREIGN KEY (`alarm_type_id`) REFERENCES `alarm_type` (`id`) ON DELETE CASCADE,
              DROP COLUMN `alarm_type_code`
        """))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0047")
