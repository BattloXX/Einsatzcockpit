"""Geräte-Login: is_device flag on user, device_token table

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-29
"""
from alembic import op
from sqlalchemy import text

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("""
        ALTER TABLE `user`
        ADD COLUMN IF NOT EXISTS `is_device` TINYINT(1) NOT NULL DEFAULT 0
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `device_token` (
            `id`           BIGINT        NOT NULL AUTO_INCREMENT,
            `label`        VARCHAR(150)  NOT NULL,
            `token_hash`   VARCHAR(64)   NOT NULL,
            `user_id`      BIGINT        NOT NULL,
            `created_at`   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `last_used_at` DATETIME      NULL,
            `revoked_at`   DATETIME      NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_device_token_hash` (`token_hash`),
            CONSTRAINT `fk_device_token_user`
                FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade():
    op.execute(text("DROP TABLE IF EXISTS `device_token`"))
    op.execute(text("ALTER TABLE `user` DROP COLUMN IF EXISTS `is_device`"))
