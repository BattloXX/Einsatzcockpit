"""BMA-Webplattform-Import (Landeswarnzentrale Vorarlberg): Org-Konfiguration,
Dedup-/Diff-Merker je Anlage, Laufprotokoll, sowie externe Identitaet an
objekt_kontakt (damit importierte Kontakte von haendisch gepflegten
unterscheidbar bleiben - siehe app/models/bma_import.py).

Revision ID: 0181
Revises: 0180
Create Date: 2026-07-26
"""
from sqlalchemy import text

from alembic import op

revision = "0181"
down_revision = "0180"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # objekt_kontakt: extern_quelle IS NULL bleibt haendisch gepflegt und wird vom
    # Importer nie angefasst; extern_quelle='dibos_bma' + extern_id="{personId}:
    # {kontakttypId}" identifiziert einen vom Import verwalteten Kontakt.
    op.execute(text("""
        ALTER TABLE `objekt_kontakt`
            ADD COLUMN `extern_quelle` VARCHAR(20) NULL AFTER `sort`
    """))
    op.execute(text("""
        ALTER TABLE `objekt_kontakt`
            ADD COLUMN `extern_id` VARCHAR(100) NULL AFTER `extern_quelle`
    """))
    op.execute(text("""
        CREATE INDEX `ix_objekt_kontakt_extern`
            ON `objekt_kontakt` (`org_id`, `extern_quelle`, `extern_id`)
    """))

    op.execute(text("""
        CREATE TABLE `org_bma_import_config` (
            `id`                       INT          NOT NULL AUTO_INCREMENT,
            `org_id`                   BIGINT       NOT NULL,
            `enabled`                  BOOLEAN      NOT NULL DEFAULT FALSE,
            `base_url`                 VARCHAR(300) NULL,
            `session_cookie_enc`       TEXT         NULL,
            `session_gesetzt_am`       DATETIME     NULL,
            `keepalive_aktiv`          BOOLEAN      NOT NULL DEFAULT TRUE,
            `sync_stunde`              INT          NOT NULL DEFAULT 3,
            `sync_minute`              INT          NOT NULL DEFAULT 30,
            `auto_anlegen`             BOOLEAN      NOT NULL DEFAULT TRUE,
            `import_rechnungsadresse`  BOOLEAN      NOT NULL DEFAULT FALSE,
            `letzter_lauf_am`          DATETIME     NULL,
            `letzter_lauf_status`      VARCHAR(20)  NULL,
            `letzter_lauf_meldung`     VARCHAR(300) NULL,
            `created_at`               DATETIME     NOT NULL,
            `updated_at`               DATETIME     NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_org_bma_import_config_org` (`org_id`),
            CONSTRAINT `fk_org_bma_import_config_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE `bma_import_satz` (
            `id`                   BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`               BIGINT       NULL,
            `extern_id`            VARCHAR(50)  NOT NULL,
            `extern_guid`          VARCHAR(64)  NULL,
            `objekt_id`            BIGINT       NULL,
            `bma_nummer`           VARCHAR(50)  NULL,
            `bezeichnung`          VARCHAR(200) NULL,
            `rohdaten_json`        LONGTEXT     NULL,
            `quell_hash`           VARCHAR(64)  NULL,
            `bestaetigt_hash`      VARCHAR(64)  NULL,
            `quell_change_date`    DATETIME     NULL,
            `detail_geholt_am`     DATETIME     NULL,
            `zuordnung`            VARCHAR(20)  NOT NULL DEFAULT 'offen',
            `status`               VARCHAR(20)  NOT NULL DEFAULT 'aktiv',
            `erst_gesehen_am`      DATETIME     NOT NULL,
            `zuletzt_gesehen_am`   DATETIME     NOT NULL,
            `zuletzt_geaendert_am` DATETIME     NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_bma_import_satz_org_extern` (`org_id`, `extern_id`),
            INDEX `ix_bma_import_satz_org_id` (`org_id`),
            INDEX `ix_bma_import_satz_org_status` (`org_id`, `status`),
            CONSTRAINT `fk_bma_import_satz_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_bma_import_satz_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE `bma_import_lauf` (
            `id`             BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`         BIGINT       NULL,
            `gestartet_am`   DATETIME     NOT NULL,
            `beendet_am`     DATETIME     NULL,
            `ausloeser`      VARCHAR(20)  NOT NULL DEFAULT 'loop',
            `status`         VARCHAR(20)  NOT NULL DEFAULT 'ok',
            `gefunden`       INT          NOT NULL DEFAULT 0,
            `neu_angelegt`   INT          NOT NULL DEFAULT 0,
            `aktualisiert`   INT          NOT NULL DEFAULT 0,
            `vorschlaege`    INT          NOT NULL DEFAULT 0,
            `uebersprungen`  INT          NOT NULL DEFAULT 0,
            `fehler`         INT          NOT NULL DEFAULT 0,
            `meldung`        VARCHAR(500) NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_bma_import_lauf_org_id` (`org_id`),
            INDEX `ix_bma_import_lauf_org_gestartet` (`org_id`, `gestartet_am`),
            CONSTRAINT `fk_bma_import_lauf_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `bma_import_lauf`"))
    op.execute(text("DROP TABLE IF EXISTS `bma_import_satz`"))
    op.execute(text("DROP TABLE IF EXISTS `org_bma_import_config`"))
    op.execute(text("ALTER TABLE `objekt_kontakt` DROP INDEX `ix_objekt_kontakt_extern`"))
    op.execute(text("ALTER TABLE `objekt_kontakt` DROP COLUMN `extern_id`"))
    op.execute(text("ALTER TABLE `objekt_kontakt` DROP COLUMN `extern_quelle`"))
