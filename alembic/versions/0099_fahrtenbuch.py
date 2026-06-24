"""Digitales Fahrten- & Betriebsbuch

Revision ID: 0099
Revises: 0098
Create Date: 2026-06-24
"""
from alembic import op
from sqlalchemy import text

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade():
    # VehicleMaster: Fahrtenbuch-Felder
    op.execute(text("""
        ALTER TABLE `vehicle_master`
        ADD COLUMN IF NOT EXISTS `kennzeichen`                    VARCHAR(20)     NULL,
        ADD COLUMN IF NOT EXISTS `km_aktuell`                     INT             NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `betriebsstunden_aktuell`        DECIMAL(10,1)   NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `seilwinde_bh_aktuell`           DECIMAL(10,1)   NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `erfasst_km`                     TINYINT(1)      NOT NULL DEFAULT 1,
        ADD COLUMN IF NOT EXISTS `erfasst_betriebsstunden`        TINYINT(1)      NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `zweiter_maschinist_pflicht`     TINYINT(1)      NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `seilwinde_abfrage`              TINYINT(1)      NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `warn_schwelle_km`               INT             NOT NULL DEFAULT 50,
        ADD COLUMN IF NOT EXISTS `warn_schwelle_bh`               DECIMAL(6,1)    NOT NULL DEFAULT 10,
        ADD COLUMN IF NOT EXISTS `qr_token`                       VARCHAR(40)     NULL,
        ADD COLUMN IF NOT EXISTS `schaden_mail_override`          VARCHAR(255)    NULL,
        ADD COLUMN IF NOT EXISTS `schaden_teams_webhook_override` VARCHAR(1000)   NULL
    """))
    op.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS `ix_vehicle_master_qr_token` ON `vehicle_master` (`qr_token`)"))

    # OrgSettings: Fahrtenbuch-Konfiguration
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `fahrtenbuch_token`     VARCHAR(40)   NULL,
        ADD COLUMN IF NOT EXISTS `schaden_mail`          VARCHAR(255)  NULL,
        ADD COLUMN IF NOT EXISTS `schaden_teams_webhook_url` VARCHAR(1000) NULL,
        ADD COLUMN IF NOT EXISTS `fahrt_doppel_minuten`  INT           NOT NULL DEFAULT 10
    """))
    op.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS `ix_org_settings_fahrtenbuch_token` ON `org_settings` (`fahrtenbuch_token`)"))

    # Member: Gruppenkommandant-Flag
    op.execute(text("""
        ALTER TABLE `member`
        ADD COLUMN IF NOT EXISTS `ist_gruppenkommandant` TINYINT(1) NOT NULL DEFAULT 0
    """))

    # Enum-Typen (MariaDB verwendet VARCHAR mit Check oder ENUM)
    # Tabelle: fahrtzweck
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `fahrtzweck` (
            `id`                           BIGINT        NOT NULL AUTO_INCREMENT,
            `org_id`                       INT           NULL,
            `name`                         VARCHAR(120)  NOT NULL,
            `kategorie`                    VARCHAR(20)   NOT NULL,
            `verlangt_ausbildner`          TINYINT(1)    NOT NULL DEFAULT 0,
            `verlangt_gruppenkommandant`   TINYINT(1)    NOT NULL DEFAULT 0,
            `aktiv`                        TINYINT(1)    NOT NULL DEFAULT 1,
            `sort`                         INT           NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            INDEX `ix_fahrtzweck_org_id` (`org_id`),
            CONSTRAINT `fk_fahrtzweck_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # Tabelle: zielort
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `zielort` (
            `id`      BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`  INT          NULL,
            `name`    VARCHAR(200) NOT NULL,
            `aktiv`   TINYINT(1)   NOT NULL DEFAULT 1,
            `sort`    INT          NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            INDEX `ix_zielort_org_id` (`org_id`),
            CONSTRAINT `fk_zielort_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # Tabelle: fahrt
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `fahrt` (
            `id`                              BIGINT        NOT NULL AUTO_INCREMENT,
            `org_id`                          BIGINT        NOT NULL,
            `created_at`                      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at`                      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `zeitpunkt`                       DATETIME      NOT NULL,
            `fahrzeug_id`                     BIGINT        NOT NULL,
            `maschinist_member_id`            BIGINT        NULL,
            `maschinist_name`                 VARCHAR(160)  NOT NULL,
            `maschinist2_member_id`           BIGINT        NULL,
            `maschinist2_name`                VARCHAR(160)  NULL,
            `km_stand_neu`                    INT           NULL,
            `km_delta`                        INT           NULL,
            `km_warnung_bestaetigt`           TINYINT(1)    NOT NULL DEFAULT 0,
            `betriebsstunden_neu`             DECIMAL(10,1) NULL,
            `betriebsstunden_delta`           DECIMAL(10,1) NULL,
            `bh_warnung_bestaetigt`           TINYINT(1)    NOT NULL DEFAULT 0,
            `seilwinde_bh_neu`                DECIMAL(10,1) NULL,
            `seilwinde_bh_delta`              DECIMAL(10,1) NULL,
            `seilwinde_warnung_bestaetigt`    TINYINT(1)    NOT NULL DEFAULT 0,
            `seilwinde_bediener_member_id`    BIGINT        NULL,
            `seilwinde_bediener_name`         VARCHAR(160)  NULL,
            `zielort_id`                      BIGINT        NULL,
            `zielort_freitext`                VARCHAR(200)  NULL,
            `zweck_id`                        BIGINT        NOT NULL,
            `fahrttyp`                        VARCHAR(20)   NOT NULL,
            `incident_id`                     BIGINT        NULL,
            `ausbildner_member_id`            BIGINT        NULL,
            `ausbildner_name`                 VARCHAR(160)  NULL,
            `gruppenkommandant_member_id`     BIGINT        NULL,
            `gruppenkommandant_name`          VARCHAR(160)  NULL,
            `schaden_vorhanden`               TINYINT(1)    NOT NULL DEFAULT 0,
            `schaden_betriebsfaehig`          TINYINT(1)    NULL,
            `schaden_beschreibung`            TEXT          NULL,
            `bemerkung`                       TEXT          NULL,
            `nicht_statistikrelevant`         TINYINT(1)    NOT NULL DEFAULT 0,
            `status`                          VARCHAR(20)   NOT NULL DEFAULT 'aktiv',
            `original_fahrt_id`               BIGINT        NULL,
            `ersetzt_durch_id`                BIGINT        NULL,
            `storno_grund`                    TEXT          NULL,
            `geaendert_von_user_id`           BIGINT        NULL,
            `erfasst_von_user_id`             BIGINT        NULL,
            `erfasst_via`                     VARCHAR(10)   NOT NULL DEFAULT 'web',
            `token_label`                     VARCHAR(120)  NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_fahrt_org_zeitpunkt` (`org_id`, `zeitpunkt`),
            INDEX `ix_fahrt_fahrzeug`      (`fahrzeug_id`),
            INDEX `ix_fahrt_status`        (`status`),
            INDEX `ix_fahrt_maschinist`    (`maschinist_member_id`),
            CONSTRAINT `fk_fahrt_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_fahrt_fahrzeug`
                FOREIGN KEY (`fahrzeug_id`) REFERENCES `vehicle_master`(`id`) ON DELETE RESTRICT,
            CONSTRAINT `fk_fahrt_maschinist`
                FOREIGN KEY (`maschinist_member_id`) REFERENCES `member`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_maschinist2`
                FOREIGN KEY (`maschinist2_member_id`) REFERENCES `member`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_seilwinde_bediener`
                FOREIGN KEY (`seilwinde_bediener_member_id`) REFERENCES `member`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_zielort`
                FOREIGN KEY (`zielort_id`) REFERENCES `zielort`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_zweck`
                FOREIGN KEY (`zweck_id`) REFERENCES `fahrtzweck`(`id`) ON DELETE RESTRICT,
            CONSTRAINT `fk_fahrt_incident`
                FOREIGN KEY (`incident_id`) REFERENCES `incident`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_ausbildner`
                FOREIGN KEY (`ausbildner_member_id`) REFERENCES `member`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_gk`
                FOREIGN KEY (`gruppenkommandant_member_id`) REFERENCES `member`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_original`
                FOREIGN KEY (`original_fahrt_id`) REFERENCES `fahrt`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_ersetzt_durch`
                FOREIGN KEY (`ersetzt_durch_id`) REFERENCES `fahrt`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_geaendert_von`
                FOREIGN KEY (`geaendert_von_user_id`) REFERENCES `user`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_fahrt_erfasst_von`
                FOREIGN KEY (`erfasst_von_user_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # Tabelle: fahrt_benachrichtigung
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `fahrt_benachrichtigung` (
            `id`          BIGINT        NOT NULL AUTO_INCREMENT,
            `fahrt_id`    BIGINT        NOT NULL,
            `org_id`      BIGINT        NOT NULL,
            `kanal`       VARCHAR(10)   NOT NULL,
            `empfaenger`  VARCHAR(1000) NOT NULL,
            `status`      VARCHAR(10)   NOT NULL,
            `fehlertext`  TEXT          NULL,
            `gesendet_am` DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            INDEX `ix_fahrt_benachrichtigung_fahrt` (`fahrt_id`),
            CONSTRAINT `fk_fahrt_benachrichtigung_fahrt`
                FOREIGN KEY (`fahrt_id`) REFERENCES `fahrt`(`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_fahrt_benachrichtigung_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade():
    op.execute(text("DROP TABLE IF EXISTS `fahrt_benachrichtigung`"))
    op.execute(text("DROP TABLE IF EXISTS `fahrt`"))
    op.execute(text("DROP TABLE IF EXISTS `zielort`"))
    op.execute(text("DROP TABLE IF EXISTS `fahrtzweck`"))
    op.execute(text("ALTER TABLE `member` DROP COLUMN IF EXISTS `ist_gruppenkommandant`"))
    op.execute(text("""
        ALTER TABLE `org_settings`
        DROP COLUMN IF EXISTS `fahrtenbuch_token`,
        DROP COLUMN IF EXISTS `schaden_mail`,
        DROP COLUMN IF EXISTS `schaden_teams_webhook_url`,
        DROP COLUMN IF EXISTS `fahrt_doppel_minuten`
    """))
    op.execute(text("""
        ALTER TABLE `vehicle_master`
        DROP COLUMN IF EXISTS `kennzeichen`,
        DROP COLUMN IF EXISTS `km_aktuell`,
        DROP COLUMN IF EXISTS `betriebsstunden_aktuell`,
        DROP COLUMN IF EXISTS `seilwinde_bh_aktuell`,
        DROP COLUMN IF EXISTS `erfasst_km`,
        DROP COLUMN IF EXISTS `erfasst_betriebsstunden`,
        DROP COLUMN IF EXISTS `zweiter_maschinist_pflicht`,
        DROP COLUMN IF EXISTS `seilwinde_abfrage`,
        DROP COLUMN IF EXISTS `warn_schwelle_km`,
        DROP COLUMN IF EXISTS `warn_schwelle_bh`,
        DROP COLUMN IF EXISTS `qr_token`,
        DROP COLUMN IF EXISTS `schaden_mail_override`,
        DROP COLUMN IF EXISTS `schaden_teams_webhook_override`
    """))
