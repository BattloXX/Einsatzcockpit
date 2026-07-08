"""OrgSettings: Verleihschein automatisch am Stationsdrucker drucken

Revision ID: 0146
Revises: 0145
Create Date: 2026-07-08
"""
from sqlalchemy import text

from alembic import op

revision = "0146"
down_revision = "0145"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE `org_settings` "
        "ADD COLUMN IF NOT EXISTS `verleih_autodruck` TINYINT(1) NOT NULL DEFAULT 0"
    ))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `verleih_autodruck`"))
