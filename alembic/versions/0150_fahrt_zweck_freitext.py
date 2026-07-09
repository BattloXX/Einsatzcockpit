"""Fahrt: Freitext-Zweck für Kategorie "sonstige"

Revision ID: 0150
Revises: 0149
Create Date: 2026-07-09
"""
from alembic import op
from sqlalchemy import text

revision = "0150"
down_revision = "0149"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("""
        ALTER TABLE `fahrt`
        ADD COLUMN IF NOT EXISTS `zweck_freitext` VARCHAR(200) NULL
    """))


def downgrade():
    op.execute(text("""
        ALTER TABLE `fahrt`
        DROP COLUMN IF EXISTS `zweck_freitext`
    """))
