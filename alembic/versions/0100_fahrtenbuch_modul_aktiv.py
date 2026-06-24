"""Fahrtenbuch-Modul-Flag je Organisation

Revision ID: 0100
Revises: 0099
Create Date: 2026-06-24
"""
from alembic import op
from sqlalchemy import text

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `fahrtenbuch_modul_aktiv` TINYINT(1) NOT NULL DEFAULT 0
    """))


def downgrade():
    op.execute(text("""
        ALTER TABLE `org_settings`
        DROP COLUMN IF EXISTS `fahrtenbuch_modul_aktiv`
    """))
