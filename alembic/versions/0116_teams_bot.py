"""Teams-Alarmierung: TeamsAlarmConfig, TeamsChannelBinding, TeamsCardPost, AlarmToken,
Teilnahme-RSVP-Spalten

Revision ID: 0116
Revises: 0115
Create Date: 2026-07-04
"""
from alembic import op
from sqlalchemy import text

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Teams-Alarmierungs-Konfiguration je Org (Basis-Webhook + optionale Bot-Erweiterung) ──
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `teams_alarm_config` (
            `id`                     BIGINT        NOT NULL AUTO_INCREMENT,
            `org_id`                 BIGINT        NOT NULL,
            `enabled`                TINYINT(1)    NOT NULL DEFAULT 0,
            `send_exercise`          TINYINT(1)    NOT NULL DEFAULT 0,
            `include_map`            TINYINT(1)    NOT NULL DEFAULT 1,
            `include_gmaps_link`     TINYINT(1)    NOT NULL DEFAULT 1,
            `include_qr_link`        TINYINT(1)    NOT NULL DEFAULT 1,
            `webhook_url_alarm`      VARCHAR(1000) NULL,
            `webhook_url_uebung`     VARCHAR(1000) NULL,
            `bot_enabled`            TINYINT(1)    NOT NULL DEFAULT 0,
            `bot_app_id`             VARCHAR(100)  NULL,
            `bot_tenant_id`          VARCHAR(100)  NULL,
            `bot_client_secret_enc`  LONGTEXT      NULL,
            `created_at`             DATETIME      NOT NULL,
            `updated_at`             DATETIME      NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_teams_alarm_config_org` (`org_id`),
            CONSTRAINT `fk_teams_alarm_config_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # ── Kanalbindungen (nur relevant wenn bot_enabled) — je Org bis zu 2 Zeilen ──
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `teams_channel_binding` (
            `id`               BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`           BIGINT       NOT NULL,
            `target`           ENUM('alarm','uebung') NOT NULL,
            `service_url`      VARCHAR(300) NOT NULL,
            `conversation_id`  VARCHAR(300) NOT NULL,
            `team_id`          VARCHAR(300) NULL,
            `bot_id`           VARCHAR(100) NULL,
            `channel_name`     VARCHAR(200) NULL,
            `captured_at`      DATETIME     NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_teams_binding_org_target` (`org_id`, `target`),
            CONSTRAINT `fk_teams_channel_binding_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # ── Protokoll gesendeter Alarmkarten (fuer In-Place-Refresh bei Zusage/Absage) ──
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `teams_card_post` (
            `id`               BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`           BIGINT       NOT NULL,
            `incident_id`      BIGINT       NOT NULL,
            `target`           ENUM('alarm','uebung') NOT NULL,
            `conversation_id`  VARCHAR(300) NOT NULL,
            `activity_id`      VARCHAR(300) NULL,
            `created_at`       DATETIME     NOT NULL,
            PRIMARY KEY (`id`),
            CONSTRAINT `fk_teams_card_post_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_teams_card_post_incident` FOREIGN KEY (`incident_id`)
                REFERENCES `incident` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_teams_card_post_incident
        ON teams_card_post (incident_id)
    """))

    # ── Anonymer Read-Only-Token je Einsatz fuer oeffentliche Alarmuebersicht + Kartenbild ──
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `alarm_token` (
            `id`           BIGINT      NOT NULL AUTO_INCREMENT,
            `incident_id`  BIGINT      NOT NULL,
            `token_hash`   VARCHAR(64) NOT NULL,
            `created_at`   DATETIME    NOT NULL,
            `revoked_at`   DATETIME    NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_alarm_token_incident` (`incident_id`),
            UNIQUE KEY `uq_alarm_token_hash` (`token_hash`),
            CONSTRAINT `fk_alarm_token_incident` FOREIGN KEY (`incident_id`)
                REFERENCES `incident` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # ── Teilnahme: RSVP-Status ueber Teams (Zusage/Absage vor dem Einsatz) ──
    op.execute(text("""
        ALTER TABLE `teilnahme`
        ADD COLUMN IF NOT EXISTS `rsvp_status` ENUM('zugesagt','abgesagt') NULL,
        ADD COLUMN IF NOT EXISTS `rsvp_at`     DATETIME NULL,
        ADD COLUMN IF NOT EXISTS `rsvp_source` VARCHAR(20) NULL
    """))

    # ── Einsatz: Plain-Text-Token fuer die oeffentliche Alarmuebersicht (analog
    # auto_geojson_token) ──
    op.execute(text("""
        ALTER TABLE `incident`
        ADD COLUMN IF NOT EXISTS `alarm_token` VARCHAR(100) NULL
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `incident` DROP COLUMN IF EXISTS `alarm_token`"))

    op.execute(text("""
        ALTER TABLE `teilnahme`
        DROP COLUMN IF EXISTS `rsvp_status`,
        DROP COLUMN IF EXISTS `rsvp_at`,
        DROP COLUMN IF EXISTS `rsvp_source`
    """))

    op.execute(text("DROP TABLE IF EXISTS `alarm_token`"))

    op.execute(text("DROP INDEX IF EXISTS ix_teams_card_post_incident ON teams_card_post"))
    op.execute(text("DROP TABLE IF EXISTS `teams_card_post`"))

    op.execute(text("DROP TABLE IF EXISTS `teams_channel_binding`"))

    op.execute(text("DROP TABLE IF EXISTS `teams_alarm_config`"))
