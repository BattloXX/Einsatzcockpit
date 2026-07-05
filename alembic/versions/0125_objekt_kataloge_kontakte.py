"""Objektverwaltung PR2: Gefahren-/Merkmal-Katalog, Kontakte, Wohnanlage

- gefahren_katalog + objekt_gefahr: strukturierte Gefahren mit Piktogramm-Typ
- merkmal_katalog + objekt_merkmal: erweiterbare Objektmerkmale mit Hinweis
- objekt_kontakt: Ansprechpartner mit Mehrfach-Telefonen (JSON)
- objekt_wohnanlage: Wohnanlagen-Zusatzblock (1:1)
- Seeds fuer Gefahren- und Merkmal-Katalog je bestehender Org

Revision ID: 0125
Revises: 0124
Create Date: 2026-07-05
"""
from sqlalchemy import text

from alembic import op

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None

# (name, piktogramm_typ)
_STANDARD_GEFAHREN = [
    ("EX-Bereich", "ex"),
    ("Gasanschluss / Gasflaschen", "gas"),
    ("Chemie / Gefahrstoff", "chemie"),
    ("Hochspannung", "hochspannung"),
    ("Photovoltaikanlage", "pv"),
    ("Ammoniak (NH3)", "nh3"),
    ("Hohe Brandlast", "brandlast"),
]

# (code, name, icon)
_STANDARD_MERKMALE = [
    ("schluesselbox", "Schlüsselbox", "🔑"),
    ("brandschutzplan", "Brandschutzplan vorhanden", "📕"),
    ("dlk_stellplatz", "Drehleiterstellplatz", "🚒"),
    ("objektfunk", "Objektfunkanlage", "📻"),
    ("tiefgarage", "Tiefgarage", "🅿️"),
    ("pv", "Photovoltaikanlage", "☀️"),
    ("feuerwehraufzug", "Lift / Feuerwehraufzug", "🛗"),
    ("sammelplatz", "Sammelplatz", "🚻"),
    ("gas", "Gasanschluss", "🔥"),
    ("sprinkler", "Sprinkleranlage", "💧"),
    ("rwa", "RWA (Rauch-/Wärmeabzug)", "🌀"),
]


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `gefahren_katalog` (
            `id`             BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`         BIGINT       NULL,
            `name`           VARCHAR(100) NOT NULL,
            `piktogramm_typ` VARCHAR(30)  NOT NULL DEFAULT 'sonstig',
            `sort`           INT          NOT NULL DEFAULT 0,
            `aktiv`          TINYINT(1)   NOT NULL DEFAULT 1,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_gefahren_katalog_org_name` (`org_id`, `name`),
            INDEX `ix_gefahren_katalog_org_id` (`org_id`),
            CONSTRAINT `fk_gefahren_katalog_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_gefahr` (
            `id`         BIGINT      NOT NULL AUTO_INCREMENT,
            `org_id`     BIGINT      NULL,
            `objekt_id`  BIGINT      NOT NULL,
            `gefahr_id`  BIGINT      NOT NULL,
            `un_nummer`  VARCHAR(10) NULL,
            `detail`     LONGTEXT    NULL,
            `sort`       INT         NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            INDEX `ix_objekt_gefahr_org_id` (`org_id`),
            INDEX `ix_objekt_gefahr_org_objekt` (`org_id`, `objekt_id`),
            CONSTRAINT `fk_objekt_gefahr_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_gefahr_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_gefahr_katalog` FOREIGN KEY (`gefahr_id`)
                REFERENCES `gefahren_katalog` (`id`) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `merkmal_katalog` (
            `id`      BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`  BIGINT       NULL,
            `code`    VARCHAR(40)  NULL,
            `name`    VARCHAR(100) NOT NULL,
            `icon`    VARCHAR(40)  NULL,
            `sort`    INT          NOT NULL DEFAULT 0,
            `aktiv`   TINYINT(1)   NOT NULL DEFAULT 1,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_merkmal_katalog_org_name` (`org_id`, `name`),
            INDEX `ix_merkmal_katalog_org_id` (`org_id`),
            CONSTRAINT `fk_merkmal_katalog_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_merkmal` (
            `id`          BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`      BIGINT       NULL,
            `objekt_id`   BIGINT       NOT NULL,
            `merkmal_id`  BIGINT       NOT NULL,
            `hinweis`     VARCHAR(300) NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_objekt_merkmal` (`objekt_id`, `merkmal_id`),
            INDEX `ix_objekt_merkmal_org_id` (`org_id`),
            CONSTRAINT `fk_objekt_merkmal_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_merkmal_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_merkmal_katalog` FOREIGN KEY (`merkmal_id`)
                REFERENCES `merkmal_katalog` (`id`) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_kontakt` (
            `id`             BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`         BIGINT       NULL,
            `objekt_id`      BIGINT       NOT NULL,
            `art`            VARCHAR(50)  NOT NULL DEFAULT 'sonstig',
            `name`           VARCHAR(150) NOT NULL,
            `telefone_json`  LONGTEXT     NULL,
            `email`          VARCHAR(200) NULL,
            `erreichbarkeit` VARCHAR(200) NULL,
            `sort`           INT          NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            INDEX `ix_objekt_kontakt_org_id` (`org_id`),
            INDEX `ix_objekt_kontakt_org_objekt` (`org_id`, `objekt_id`),
            CONSTRAINT `fk_objekt_kontakt_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_kontakt_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_wohnanlage` (
            `id`                        BIGINT   NOT NULL AUTO_INCREMENT,
            `org_id`                    BIGINT   NULL,
            `objekt_id`                 BIGINT   NOT NULL,
            `wohneinheiten`             INT      NULL,
            `geschosse`                 INT      NULL,
            `stiegen`                   INT      NULL,
            `hausverwaltung_kontakt_id` BIGINT   NULL,
            `hinweise`                  LONGTEXT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_objekt_wohnanlage_objekt` (`objekt_id`),
            INDEX `ix_objekt_wohnanlage_org_id` (`org_id`),
            CONSTRAINT `fk_objekt_wohnanlage_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_wohnanlage_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_wohnanlage_kontakt` FOREIGN KEY (`hausverwaltung_kontakt_id`)
                REFERENCES `objekt_kontakt` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # Seeds fuer bestehende Orgs
    for i, (name, typ) in enumerate(_STANDARD_GEFAHREN, start=1):
        op.execute(text(f"""
            INSERT INTO `gefahren_katalog` (`org_id`, `name`, `piktogramm_typ`, `sort`, `aktiv`)
            SELECT `id`, '{name}', '{typ}', {i}, 1
            FROM `fire_dept`
            WHERE NOT EXISTS (
                SELECT 1 FROM `gefahren_katalog` g2
                WHERE g2.`org_id` = `fire_dept`.`id` AND g2.`name` = '{name}'
            )
        """))
    for i, (code, name, icon) in enumerate(_STANDARD_MERKMALE, start=1):
        op.execute(text(f"""
            INSERT INTO `merkmal_katalog` (`org_id`, `code`, `name`, `icon`, `sort`, `aktiv`)
            SELECT `id`, '{code}', '{name}', '{icon}', {i}, 1
            FROM `fire_dept`
            WHERE NOT EXISTS (
                SELECT 1 FROM `merkmal_katalog` m2
                WHERE m2.`org_id` = `fire_dept`.`id` AND m2.`name` = '{name}'
            )
        """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `objekt_wohnanlage`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_kontakt`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_merkmal`"))
    op.execute(text("DROP TABLE IF EXISTS `merkmal_katalog`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_gefahr`"))
    op.execute(text("DROP TABLE IF EXISTS `gefahren_katalog`"))
