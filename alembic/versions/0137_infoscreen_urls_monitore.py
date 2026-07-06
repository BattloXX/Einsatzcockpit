"""Infoscreen: URL-Rotation, Monitor-Matrix, persistente URLs, GSL-Ansicht

- infoscreen_url: frei konfigurierbare Rotations-URLs je Org
- alarm_infoscreen_token: token_enc (Fernet), url_ids_json, zeigt_wetter
- org_settings.alarm_infoscreen_gsl_enabled

Revision ID: 0137
Revises: 0136
Create Date: 2026-07-06
"""
from sqlalchemy import text

from alembic import op

revision = "0137"
down_revision = "0136"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `infoscreen_url` (
            `id`        BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`    BIGINT       NULL,
            `label`     VARCHAR(120) NOT NULL,
            `url`       VARCHAR(500) NOT NULL,
            `dwell_sec` INT          NOT NULL DEFAULT 30,
            `sort`      INT          NOT NULL DEFAULT 0,
            `aktiv`     TINYINT(1)   NOT NULL DEFAULT 1,
            PRIMARY KEY (`id`),
            INDEX `ix_infoscreen_url_org` (`org_id`),
            CONSTRAINT `fk_infoscreen_url_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))
    op.execute(text(
        "ALTER TABLE `alarm_infoscreen_token` "
        "ADD COLUMN `token_enc` VARCHAR(255) NULL, "
        "ADD COLUMN `url_ids_json` LONGTEXT NULL, "
        "ADD COLUMN `zeigt_wetter` TINYINT(1) NOT NULL DEFAULT 0"
    ))
    op.execute(text(
        "ALTER TABLE `org_settings` "
        "ADD COLUMN `alarm_infoscreen_gsl_enabled` TINYINT(1) NOT NULL DEFAULT 1"
    ))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN `alarm_infoscreen_gsl_enabled`"))
    op.execute(text(
        "ALTER TABLE `alarm_infoscreen_token` "
        "DROP COLUMN `token_enc`, DROP COLUMN `url_ids_json`, DROP COLUMN `zeigt_wetter`"
    ))
    op.execute(text("DROP TABLE IF EXISTS `infoscreen_url`"))
