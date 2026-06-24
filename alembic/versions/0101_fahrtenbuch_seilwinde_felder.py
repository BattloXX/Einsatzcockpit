"""Fahrtenbuch: Seilwinde Züge und Wartung

Revision ID: 0101
Revises: 0100
Create Date: 2026-06-24
"""
from alembic import op
from sqlalchemy import text

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("""
        ALTER TABLE `fahrt`
        ADD COLUMN IF NOT EXISTS `seilwinde_zuege` INT NULL,
        ADD COLUMN IF NOT EXISTS `seilwinde_wartung` TINYINT(1) NULL
    """))


def downgrade():
    op.execute(text("""
        ALTER TABLE `fahrt`
        DROP COLUMN IF EXISTS `seilwinde_zuege`,
        DROP COLUMN IF EXISTS `seilwinde_wartung`
    """))
