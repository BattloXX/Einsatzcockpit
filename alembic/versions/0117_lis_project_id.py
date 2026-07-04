"""LIS/IPR-Anbindung: OrgLisConfig.project_id (AddSessionEntries-Key fuer GetTasks-Fix)

Revision ID: 0117
Revises: 0116
Create Date: 2026-07-04
"""
from alembic import op
from sqlalchemy import text

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_lis_config`
        ADD COLUMN IF NOT EXISTS `project_id` VARCHAR(64) NULL AFTER `organization_id`
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_lis_config` DROP COLUMN IF EXISTS `project_id`"))
