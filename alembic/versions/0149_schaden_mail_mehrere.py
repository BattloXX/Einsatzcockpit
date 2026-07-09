"""Schaden-Mail: mehrere Empfänger (Spalten verbreitern auf 500)

Revision ID: 0149
Revises: 0148
Create Date: 2026-07-09
"""
from alembic import op
from sqlalchemy import text

revision = "0149"
down_revision = "0148"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("ALTER TABLE `org_settings` MODIFY COLUMN `schaden_mail` VARCHAR(500) NULL"))
    op.execute(text("ALTER TABLE `vehicle_master` MODIFY COLUMN `schaden_mail_override` VARCHAR(500) NULL"))


def downgrade():
    op.execute(text("ALTER TABLE `org_settings` MODIFY COLUMN `schaden_mail` VARCHAR(255) NULL"))
    op.execute(text("ALTER TABLE `vehicle_master` MODIFY COLUMN `schaden_mail_override` VARCHAR(255) NULL"))
