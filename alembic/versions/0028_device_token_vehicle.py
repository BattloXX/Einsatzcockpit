"""Geräte-Login: vehicle_master_id FK auf device_token

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-29
"""
from alembic import op
from sqlalchemy import text

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("""
        ALTER TABLE `device_token`
        ADD COLUMN IF NOT EXISTS `vehicle_master_id` BIGINT NULL
    """))
    op.execute(text("""
        ALTER TABLE `device_token`
        ADD CONSTRAINT IF NOT EXISTS `fk_device_token_vehicle`
            FOREIGN KEY (`vehicle_master_id`) REFERENCES `vehicle_master`(`id`) ON DELETE SET NULL
    """))


def downgrade():
    op.execute(text("ALTER TABLE `device_token` DROP FOREIGN KEY IF EXISTS `fk_device_token_vehicle`"))
    op.execute(text("ALTER TABLE `device_token` DROP COLUMN IF EXISTS `vehicle_master_id`"))
