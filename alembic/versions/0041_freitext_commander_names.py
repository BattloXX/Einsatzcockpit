"""Freitext-Namen für EL und GK ohne Mannschaftsregister-Eintrag

Revision ID: 0041
Revises: 0040
Create Date: 2026-06-09 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text(
        "ALTER TABLE `incident` ADD COLUMN `incident_leader_name` VARCHAR(200) NULL"
    ))
    op.execute(text(
        "ALTER TABLE `incident_vehicle` ADD COLUMN `commander_name` VARCHAR(200) NULL"
    ))


def downgrade():
    op.execute(text("ALTER TABLE `incident` DROP COLUMN `incident_leader_name`"))
    op.execute(text("ALTER TABLE `incident_vehicle` DROP COLUMN `commander_name`"))
