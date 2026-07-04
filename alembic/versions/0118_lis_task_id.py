"""LIS/IPR-Anbindung: Task.lis_task_id (echte LIS-Auftraege getrennt von Meldungen)

Revision ID: 0118
Revises: 0117
Create Date: 2026-07-04
"""
from alembic import op
from sqlalchemy import text

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `task`
        ADD COLUMN IF NOT EXISTS `lis_task_id` VARCHAR(64) NULL
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_task_lis_task_id
        ON task (lis_task_id)
    """))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS ix_task_lis_task_id ON task"))
    op.execute(text("ALTER TABLE `task` DROP COLUMN IF EXISTS `lis_task_id`"))
