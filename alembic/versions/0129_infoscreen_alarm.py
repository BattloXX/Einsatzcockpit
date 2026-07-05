"""Objektverwaltung PR6: Alarm-Infoscreen (Wandmonitor)

- alarm_infoscreen_token: oeffentlicher Zugangs-Token (SHA256-Hash)
- org_settings: Idle-Modus (uhr/wetter/einsatzliste), Alarm-Anzeigedauer,
  Wetter-Infoscreen-URL fuer den Idle-Modus "wetter"

Revision ID: 0129
Revises: 0128
Create Date: 2026-07-05
"""
from sqlalchemy import text

from alembic import op

revision = "0129"
down_revision = "0128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `alarm_infoscreen_token` (
            `id`          BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`      BIGINT       NULL,
            `token_hash`  VARCHAR(64)  NOT NULL,
            `name`        VARCHAR(100) NOT NULL,
            `aktiv`       TINYINT(1)   NOT NULL DEFAULT 1,
            `erstellt_am` DATETIME     NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_alarm_infoscreen_token_hash` (`token_hash`),
            INDEX `ix_alarm_infoscreen_token_org_id` (`org_id`),
            CONSTRAINT `fk_alarm_infoscreen_token_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `alarm_infoscreen_idle_modus` VARCHAR(20) NOT NULL DEFAULT 'uhr'
    """))
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `alarm_infoscreen_alarm_dauer_min` INT NOT NULL DEFAULT 60
    """))
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `alarm_infoscreen_wetter_url` VARCHAR(500) NULL
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `alarm_infoscreen_wetter_url`"))
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `alarm_infoscreen_alarm_dauer_min`"))
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `alarm_infoscreen_idle_modus`"))
    op.execute(text("DROP TABLE IF EXISTS `alarm_infoscreen_token`"))
