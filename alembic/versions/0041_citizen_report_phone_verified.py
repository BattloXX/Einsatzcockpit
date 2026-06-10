"""Add phone_verified column to citizen_report

Revision ID: 0041
Revises: 0040
Create Date: 2026-06-10 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text(
        "ALTER TABLE `citizen_report` "
        "ADD COLUMN `phone_verified` TINYINT(1) NOT NULL DEFAULT 0"
    ))


def downgrade():
    op.execute(text("ALTER TABLE `citizen_report` DROP COLUMN `phone_verified`"))
