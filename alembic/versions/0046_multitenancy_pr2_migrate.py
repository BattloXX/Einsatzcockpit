"""Multi-tenancy PR 2 (Migrate): org_id + alarm_type_id befüllen

Revision ID: 0046
Revises: 0045
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Alle bestehenden AlarmTypes bekommen die erste fire_dept als org_id
    conn.execute(text("""
        UPDATE `alarm_type`
        SET `org_id` = (SELECT MIN(`id`) FROM `fire_dept`)
        WHERE `org_id` IS NULL
    """))

    # 2. alarm_type_id auf den 5 Junction-Tabellen via JOIN auf alarm_type.code befüllen
    for table in [
        "task_suggestion_alarm",
        "message_suggestion_alarm",
        "lage_hint_alarm",
        "default_message_alarm",
        "alarm_dispatch_vehicle",
    ]:
        conn.execute(text(f"""
            UPDATE `{table}` t
            JOIN `alarm_type` at ON t.`alarm_type_code` = at.`code`
            SET t.`alarm_type_id` = at.`id`
        """))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0046")
