"""Einsatz: Anrufername/-nummer aus dem Alarm-Webhook (Name/Telefon)

Revision ID: 0115
Revises: 0114
Create Date: 2026-07-04
"""
from alembic import op
from sqlalchemy import text

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `incident`
        ADD COLUMN IF NOT EXISTS `caller_name`  VARCHAR(200) NULL,
        ADD COLUMN IF NOT EXISTS `caller_phone` VARCHAR(50)  NULL
    """))


def downgrade() -> None:
    op.execute(text("""
        ALTER TABLE `incident`
        DROP COLUMN IF EXISTS `caller_name`,
        DROP COLUMN IF EXISTS `caller_phone`
    """))
