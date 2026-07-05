"""Objektverwaltung PR1: Kerntabellen + Modul-Flag

- objekt_kategorie: Kategorie-Katalog je Org (+ Seed-Defaults fuer bestehende Orgs)
- objekt: Kernentitaet (Nummer je Org, Adresse, Status-Workflow, Revision)
- objekt_zusatzadresse: Stiegen/Zugaenge mit eigener Adresse
- objekt_bma: BMA-/FSD-Block (1:1)
- objekt_change: feldgenaues Aenderungsprotokoll (Muster incident_change)
- org_settings.objekt_module_enabled: Org-Flag (effektiv = System AND Org)

Revision ID: 0124
Revises: 0123
Create Date: 2026-07-05
"""
from sqlalchemy import text

from alembic import op

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None

_STANDARD_KATEGORIEN = [
    "Gewerbe/Industrie",
    "Wohnanlage",
    "Öffentliches Gebäude",
    "Landwirtschaft",
    "Sonderobjekt",
]


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_kategorie` (
            `id`      BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`  BIGINT       NULL,
            `name`    VARCHAR(100) NOT NULL,
            `sort`    INT          NOT NULL DEFAULT 0,
            `aktiv`   TINYINT(1)   NOT NULL DEFAULT 1,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_objekt_kategorie_org_name` (`org_id`, `name`),
            INDEX `ix_objekt_kategorie_org_id` (`org_id`),
            CONSTRAINT `fk_objekt_kategorie_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt` (
            `id`                   BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`               BIGINT       NULL,
            `nummer`               INT          NOT NULL,
            `name`                 VARCHAR(200) NOT NULL,
            `vulgoname`            VARCHAR(200) NULL,
            `kategorie_id`         BIGINT       NULL,
            `strasse`              VARCHAR(200) NULL,
            `hausnummer`           VARCHAR(20)  NULL,
            `plz`                  VARCHAR(10)  NULL,
            `ort`                  VARCHAR(100) NULL,
            `lat`                  DOUBLE       NULL,
            `lng`                  DOUBLE       NULL,
            `informationen`        LONGTEXT     NULL,
            `anfahrtsweg`          LONGTEXT     NULL,
            `status`               VARCHAR(20)  NOT NULL DEFAULT 'entwurf',
            `revision_datum`       DATE         NULL,
            `revision_erinnert_am` DATE         NULL,
            `erstellt_am`          DATETIME     NOT NULL,
            `aktualisiert_am`      DATETIME     NOT NULL,
            `erstellt_von_id`      BIGINT       NULL,
            `aktualisiert_von_id`  BIGINT       NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_objekt_org_nummer` (`org_id`, `nummer`),
            INDEX `ix_objekt_org_id` (`org_id`),
            INDEX `ix_objekt_org_name` (`org_id`, `name`),
            INDEX `ix_objekt_org_status` (`org_id`, `status`),
            INDEX `ix_objekt_org_revision` (`org_id`, `revision_datum`),
            CONSTRAINT `fk_objekt_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_kategorie` FOREIGN KEY (`kategorie_id`)
                REFERENCES `objekt_kategorie` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_erstellt_von` FOREIGN KEY (`erstellt_von_id`)
                REFERENCES `user` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_aktualisiert_von` FOREIGN KEY (`aktualisiert_von_id`)
                REFERENCES `user` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_zusatzadresse` (
            `id`          BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`      BIGINT       NULL,
            `objekt_id`   BIGINT       NOT NULL,
            `bezeichnung` VARCHAR(100) NOT NULL,
            `strasse`     VARCHAR(200) NULL,
            `hausnummer`  VARCHAR(20)  NULL,
            `plz`         VARCHAR(10)  NULL,
            `ort`         VARCHAR(100) NULL,
            `lat`         DOUBLE       NULL,
            `lng`         DOUBLE       NULL,
            `sort`        INT          NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            INDEX `ix_objekt_zusatzadresse_org_id` (`org_id`),
            INDEX `ix_objekt_zusatzadresse_org_objekt` (`org_id`, `objekt_id`),
            CONSTRAINT `fk_objekt_zusatzadresse_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_zusatzadresse_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_bma` (
            `id`                       BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`                   BIGINT       NULL,
            `objekt_id`                BIGINT       NOT NULL,
            `bma_nummer`               VARCHAR(50)  NULL,
            `rfl_nummer`               VARCHAR(50)  NULL,
            `bmz_standort`             VARCHAR(300) NULL,
            `fbf_standort`             VARCHAR(300) NULL,
            `laufkarten_ablageort`     VARCHAR(300) NULL,
            `uebertragungseinrichtung` VARCHAR(200) NULL,
            `schluesselsafe_vorhanden` TINYINT(1)   NOT NULL DEFAULT 0,
            `schluesselsafe_standort`  VARCHAR(300) NULL,
            `schluesselsafe_inhalt`    VARCHAR(300) NULL,
            `benachrichtigung_sms`     VARCHAR(100) NULL,
            `benachrichtigung_email`   VARCHAR(200) NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_objekt_bma_objekt` (`objekt_id`),
            INDEX `ix_objekt_bma_org_id` (`org_id`),
            INDEX `ix_objekt_bma_org_nummer` (`org_id`, `bma_nummer`),
            CONSTRAINT `fk_objekt_bma_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_bma_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_change` (
            `id`          BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`      BIGINT       NULL,
            `objekt_id`   BIGINT       NOT NULL,
            `user_id`     BIGINT       NULL,
            `bereich`     VARCHAR(50)  NOT NULL,
            `feld`        VARCHAR(100) NOT NULL,
            `before_json` LONGTEXT     NULL,
            `after_json`  LONGTEXT     NULL,
            `erstellt_am` DATETIME     NOT NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_objekt_change_org_id` (`org_id`),
            INDEX `ix_objekt_change_org_objekt_ts` (`org_id`, `objekt_id`, `erstellt_am`),
            CONSTRAINT `fk_objekt_change_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_change_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_change_user` FOREIGN KEY (`user_id`)
                REFERENCES `user` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `objekt_module_enabled` TINYINT(1) NOT NULL DEFAULT 0
    """))

    # Seed Standardkategorien fuer alle bestehenden Orgs
    for i, name in enumerate(_STANDARD_KATEGORIEN, start=1):
        op.execute(text(f"""
            INSERT INTO `objekt_kategorie` (`org_id`, `name`, `sort`, `aktiv`)
            SELECT `id`, '{name}', {i}, 1
            FROM `fire_dept`
            WHERE NOT EXISTS (
                SELECT 1 FROM `objekt_kategorie` k2
                WHERE k2.`org_id` = `fire_dept`.`id`
                  AND k2.`name` = '{name}'
            )
        """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `objekt_module_enabled`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_change`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_bma`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_zusatzadresse`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_kategorie`"))
