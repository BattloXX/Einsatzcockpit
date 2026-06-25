"""Wetterwarnungen: Regeln, Zustand und Log-Tabellen

Revision ID: 0103
Revises: 0102
Create Date: 2026-06-25
"""
from alembic import op
from sqlalchemy import text

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `weather_alert_rule` (
            `id`                     BIGINT        NOT NULL AUTO_INCREMENT,
            `org_id`                 BIGINT        NULL,
            `key`                    VARCHAR(32)   NOT NULL,
            `enabled`                TINYINT(1)    NOT NULL DEFAULT 0,
            `vorwarnung`             TINYINT(1)    NOT NULL DEFAULT 1,
            `eskalation`             TINYINT(1)    NOT NULL DEFAULT 1,
            `channel_mail`           TINYINT(1)    NOT NULL DEFAULT 1,
            `channel_teams`          TINYINT(1)    NOT NULL DEFAULT 1,
            `mail_override`          VARCHAR(255)  NULL,
            `teams_webhook_override` VARCHAR(1000) NULL,
            `cooldown_min`           INT           NOT NULL DEFAULT 60,
            `params`                 JSON          NULL,
            `updated_at`             DATETIME      NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_weather_alert_rule_org_key` (`org_id`, `key`),
            INDEX `ix_weather_alert_rule_org_id` (`org_id`),
            CONSTRAINT `fk_weather_alert_rule_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `weather_alert_state` (
            `id`                   BIGINT      NOT NULL AUTO_INCREMENT,
            `org_id`               BIGINT      NULL,
            `key`                  VARCHAR(32) NOT NULL,
            `state`                VARCHAR(16) NOT NULL DEFAULT 'none',
            `since`                DATETIME    NULL,
            `last_value`           DOUBLE      NULL,
            `last_notified_at`     DATETIME    NULL,
            `last_payload_hash`    VARCHAR(64) NULL,
            `below_threshold_cycles` INT       NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_weather_alert_state_org_key` (`org_id`, `key`),
            INDEX `ix_weather_alert_state_org_id` (`org_id`),
            CONSTRAINT `fk_weather_alert_state_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `weather_alert_log` (
            `id`              BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`          BIGINT       NULL,
            `key`             VARCHAR(32)  NOT NULL,
            `state`           VARCHAR(16)  NOT NULL,
            `kanal`           VARCHAR(16)  NOT NULL,
            `empfaenger`      VARCHAR(255) NOT NULL,
            `betreff`         VARCHAR(255) NOT NULL,
            `status`          VARCHAR(16)  NOT NULL,
            `fehlertext`      VARCHAR(500) NULL,
            `payload_excerpt` VARCHAR(1000) NULL,
            `gesendet_am`     DATETIME     NOT NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_weather_alert_log_org_sent` (`org_id`, `gesendet_am`),
            CONSTRAINT `fk_weather_alert_log_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `weather_alert_log`"))
    op.execute(text("DROP TABLE IF EXISTS `weather_alert_state`"))
    op.execute(text("DROP TABLE IF EXISTS `weather_alert_rule`"))
