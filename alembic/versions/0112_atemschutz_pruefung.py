"""Atemschutzgeräteprüfung: Geräte-Stammdaten, Prüfprotokoll, Wart-Benachrichtigung

Revision ID: 0112
Revises: 0111
Create Date: 2026-07-01
"""
from alembic import op
from sqlalchemy import text

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # OrgSettings: Atemschutzgeräteprüfung-Konfiguration
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `atemschutz_pruefung_modul_aktiv`   TINYINT(1)    NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `atemschutz_pruef_token`            VARCHAR(40)   NULL,
        ADD COLUMN IF NOT EXISTS `atemschutz_wart_mail`              VARCHAR(255)  NULL,
        ADD COLUMN IF NOT EXISTS `atemschutz_wart_teams_webhook_url` VARCHAR(1000) NULL
    """))
    op.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS `ix_org_settings_atemschutz_pruef_token` "
        "ON `org_settings` (`atemschutz_pruef_token`)"
    ))

    # Tabelle: atemschutz_geraet (Stammdaten)
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `atemschutz_geraet` (
            `id`           BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`       BIGINT       NULL,
            `nummer`       VARCHAR(50)  NOT NULL,
            `bezeichnung`  VARCHAR(200) NULL,
            `aktiv`        TINYINT(1)   NOT NULL DEFAULT 1,
            `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            INDEX `ix_atemschutz_geraet_org_id` (`org_id`),
            CONSTRAINT `fk_atemschutz_geraet_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # Tabelle: atemschutz_pruefung (Prüfprotokoll)
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `atemschutz_pruefung` (
            `id`                       BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`                   BIGINT       NOT NULL,
            `geraet_id`                BIGINT       NOT NULL,
            `traeger_member_id`        BIGINT       NULL,
            `traeger_free_text`        VARCHAR(200) NULL,
            `eingesetzt_am`            DATE         NOT NULL,
            `ort_text`                 VARCHAR(200) NULL,
            `einsatz_art`              VARCHAR(20)  NOT NULL DEFAULT 'uebung',
            `incident_id`              BIGINT       NULL,
            `flasche_gewechselt`       TINYINT(1)   NOT NULL DEFAULT 0,
            `flaschendruck_bar`        INT          NULL,
            `sichtpruefung_ok`         TINYINT(1)   NOT NULL DEFAULT 1,
            `druckabfall_bar`          INT          NULL,
            `hochdruckpruefung_ok`     TINYINT(1)   NOT NULL DEFAULT 1,
            `rueckzugssignal_bar`      INT          NULL,
            `geraet_einsatzbereit_ok`  TINYINT(1)   NOT NULL DEFAULT 1,
            `defekt_info`              TEXT         NULL,
            `created_via`              VARCHAR(10)  NOT NULL DEFAULT 'public',
            `created_by_user_id`       BIGINT       NULL,
            `created_at`               DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            INDEX `ix_atemschutz_pruefung_org_id`   (`org_id`),
            INDEX `ix_atemschutz_pruefung_geraet`   (`geraet_id`),
            INDEX `ix_atemschutz_pruefung_incident` (`incident_id`),
            CONSTRAINT `fk_as_pruefung_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_as_pruefung_geraet`
                FOREIGN KEY (`geraet_id`) REFERENCES `atemschutz_geraet`(`id`) ON DELETE RESTRICT,
            CONSTRAINT `fk_as_pruefung_traeger`
                FOREIGN KEY (`traeger_member_id`) REFERENCES `member`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_as_pruefung_incident`
                FOREIGN KEY (`incident_id`) REFERENCES `incident`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_as_pruefung_user`
                FOREIGN KEY (`created_by_user_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # Tabelle: atemschutz_pruef_benachrichtigung (Audit Mail/Teams-Versand)
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `atemschutz_pruef_benachrichtigung` (
            `id`          BIGINT        NOT NULL AUTO_INCREMENT,
            `pruefung_id` BIGINT        NOT NULL,
            `org_id`      BIGINT        NOT NULL,
            `kanal`       VARCHAR(10)   NOT NULL,
            `empfaenger`  VARCHAR(1000) NOT NULL,
            `status`      VARCHAR(10)   NOT NULL,
            `fehlertext`  TEXT          NULL,
            `gesendet_am` DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            INDEX `ix_as_pruef_benachrichtigung_pruefung` (`pruefung_id`),
            CONSTRAINT `fk_as_pruef_benachrichtigung_pruefung`
                FOREIGN KEY (`pruefung_id`) REFERENCES `atemschutz_pruefung`(`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_as_pruef_benachrichtigung_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `atemschutz_pruef_benachrichtigung`"))
    op.execute(text("DROP TABLE IF EXISTS `atemschutz_pruefung`"))
    op.execute(text("DROP TABLE IF EXISTS `atemschutz_geraet`"))
    op.execute(text("""
        ALTER TABLE `org_settings`
        DROP COLUMN IF EXISTS `atemschutz_pruefung_modul_aktiv`,
        DROP COLUMN IF EXISTS `atemschutz_pruef_token`,
        DROP COLUMN IF EXISTS `atemschutz_wart_mail`,
        DROP COLUMN IF EXISTS `atemschutz_wart_teams_webhook_url`
    """))
