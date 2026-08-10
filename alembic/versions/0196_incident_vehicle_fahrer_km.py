"""Fahrer und gefahrene Kilometer je Einsatzfahrzeug

Revision ID: 0196
Revises: 0195
Create Date: 2026-08-10
"""
from sqlalchemy import text

from alembic import op

revision = "0196"
down_revision = "0195"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE `incident_vehicle` ADD COLUMN `fahrer_member_id` BIGINT NULL"))
    op.execute(text("ALTER TABLE `incident_vehicle` ADD COLUMN `fahrer_name` VARCHAR(200) NULL"))
    op.execute(text("ALTER TABLE `incident_vehicle` ADD COLUMN `fahrer2_member_id` BIGINT NULL"))
    op.execute(text("ALTER TABLE `incident_vehicle` ADD COLUMN `fahrer2_name` VARCHAR(200) NULL"))
    op.execute(text("ALTER TABLE `incident_vehicle` ADD COLUMN `km_gefahren` INT NULL"))
    op.execute(text(
        "ALTER TABLE `incident_vehicle` ADD CONSTRAINT `fk_incident_vehicle_fahrer` "
        "FOREIGN KEY (`fahrer_member_id`) REFERENCES `member`(`id`) ON DELETE SET NULL"
    ))
    op.execute(text(
        "ALTER TABLE `incident_vehicle` ADD CONSTRAINT `fk_incident_vehicle_fahrer2` "
        "FOREIGN KEY (`fahrer2_member_id`) REFERENCES `member`(`id`) ON DELETE SET NULL"
    ))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `incident_vehicle` DROP FOREIGN KEY `fk_incident_vehicle_fahrer2`"))
    op.execute(text("ALTER TABLE `incident_vehicle` DROP FOREIGN KEY `fk_incident_vehicle_fahrer`"))
    op.execute(text("ALTER TABLE `incident_vehicle` DROP COLUMN `km_gefahren`"))
    op.execute(text("ALTER TABLE `incident_vehicle` DROP COLUMN `fahrer2_name`"))
    op.execute(text("ALTER TABLE `incident_vehicle` DROP COLUMN `fahrer2_member_id`"))
    op.execute(text("ALTER TABLE `incident_vehicle` DROP COLUMN `fahrer_name`"))
    op.execute(text("ALTER TABLE `incident_vehicle` DROP COLUMN `fahrer_member_id`"))
