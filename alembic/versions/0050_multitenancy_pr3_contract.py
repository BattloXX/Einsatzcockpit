"""Multi-tenancy PR 3 (Contract): org_id NOT NULL + FK auf Vorlagen-Tabellen

Revision ID: 0050
Revises: 0049
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    for table in ["task_suggestion", "message_suggestion", "lage_hint", "default_message"]:
        conn.execute(text(
            f"ALTER TABLE `{table}`"
            f"  MODIFY COLUMN `org_id` INT NOT NULL,"
            f"  ADD CONSTRAINT `fk_{table}_org_id`"
            f"    FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE CASCADE"
        ))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0050")
