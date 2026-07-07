"""ECPG Print & Alarm Gateway: gateway + printer + OrgSettings-Flags

Revision ID: 0141
Revises: 0140
Create Date: 2026-07-07
"""
from sqlalchemy import text

from alembic import op

revision = "0141"
down_revision = "0140"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `gateway` (
            `id`                 BIGINT NOT NULL AUTO_INCREMENT,
            `org_id`             BIGINT NULL,
            `name`               VARCHAR(150) NOT NULL,
            `standort`           VARCHAR(200) NULL,
            `device_token_hash`  VARCHAR(64) NULL,
            `pairing_code_hash`  VARCHAR(64) NULL,
            `pairing_expires_at` DATETIME NULL,
            `status`             VARCHAR(20) NOT NULL DEFAULT 'unpaired',
            `last_seen_at`       DATETIME NULL,
            `version`            VARCHAR(40) NULL,
            `serial_connected`   TINYINT(1) NOT NULL DEFAULT 0,
            `offline_alerted_at` DATETIME NULL,
            `wut_config`         JSON NULL,
            `parser_config`      JSON NULL,
            `erstellt_am`        DATETIME NULL,
            `aktualisiert_am`    DATETIME NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_gateway_device_token` (`device_token_hash`),
            KEY `ix_gateway_org` (`org_id`),
            CONSTRAINT `fk_gateway_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `printer` (
            `id`            BIGINT NOT NULL AUTO_INCREMENT,
            `org_id`        BIGINT NULL,
            `gateway_id`    BIGINT NOT NULL,
            `name`          VARCHAR(150) NOT NULL,
            `modell`        VARCHAR(150) NULL,
            `uri`           VARCHAR(300) NOT NULL,
            `identity`      JSON NULL,
            `capabilities`  JSON NULL,
            `defaults`      JSON NULL,
            `aktiv`         TINYINT(1) NOT NULL DEFAULT 0,
            `status`        JSON NULL,
            `discovered_at` DATETIME NULL,
            `activated_at`  DATETIME NULL,
            `erstellt_am`   DATETIME NULL,
            PRIMARY KEY (`id`),
            KEY `ix_printer_org_gateway` (`org_id`, `gateway_id`),
            CONSTRAINT `fk_printer_gateway`
                FOREIGN KEY (`gateway_id`) REFERENCES `gateway` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_printer_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    op.execute(text(
        "ALTER TABLE `org_settings` "
        "ADD COLUMN `gateway_module_enabled` TINYINT(1) NOT NULL DEFAULT 0, "
        "ADD COLUMN `gateway_offline_alert_min` INT NOT NULL DEFAULT 15"
    ))


def downgrade() -> None:
    op.execute(text(
        "ALTER TABLE `org_settings` "
        "DROP COLUMN `gateway_module_enabled`, DROP COLUMN `gateway_offline_alert_min`"
    ))
    op.execute(text("DROP TABLE IF EXISTS `printer`"))
    op.execute(text("DROP TABLE IF EXISTS `gateway`"))
