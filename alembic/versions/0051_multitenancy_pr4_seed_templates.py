"""Multi-tenancy PR 4: SeedTemplate-Tabelle + FireDept.deleted_at

Revision ID: 0051
Revises: 0050
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. seed_template Tabelle
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS `seed_template` (
          `id`            INT          NOT NULL AUTO_INCREMENT,
          `profile`       VARCHAR(50)  NOT NULL,
          `profile_label` VARCHAR(100) NOT NULL,
          `type`          VARCHAR(30)  NOT NULL,
          `data`          LONGTEXT     NOT NULL,
          `display_order` INT          NOT NULL DEFAULT 0,
          `updated_at`    DATETIME     NOT NULL,
          PRIMARY KEY (`id`),
          INDEX `ix_seed_template_profile` (`profile`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # 2. FireDept.deleted_at (Soft-Delete)
    conn.execute(text(
        "ALTER TABLE `fire_dept` ADD COLUMN `deleted_at` DATETIME NULL DEFAULT NULL"
    ))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0051")
