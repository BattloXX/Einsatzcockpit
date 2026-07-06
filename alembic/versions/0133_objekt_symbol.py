"""Objektverwaltung: pflegbarer Karten-Symbol-Katalog (inkl. Bild-Upload)

- objekt_symbol: Symbolkatalog je Org (Code, Name, Stil, Text, Bildpfad)
- Seeds je bestehender Org aus der bisherigen Konstante OBJEKT_SYMBOL_TYPEN (system=1)

Revision ID: 0133
Revises: 0132
Create Date: 2026-07-06
"""
from sqlalchemy import text

from alembic import op

revision = "0133"
down_revision = "0132"
branch_labels = None
depends_on = None

# (code, name, text, stil) — identisch zu OBJEKT_SYMBOL_TYPEN + JS-SYMBOLE-Map
_SEED = [
    ("fsd", "FSD / Schlüsselsafe", "FSD", "box"),
    ("schluesselbox", "Schlüsselbox", "BOX", "box"),
    ("bsp", "Brandschutzplan (Ablage)", "BSP", "box"),
    ("bmz", "BMZ (Brandmelderzentrale)", "BMZ", "box"),
    ("fbf", "FBF (Feuerwehr-Bedienfeld)", "FBF", "box"),
    ("dlk_stellplatz", "Drehleiter-Stellplatz", "DLK", "box"),
    ("objektfunk", "Objektfunk-Bedienfeld", "FUNK", "box"),
    ("sammelplatz", "Sammelplatz", "SP", "gruen"),
    ("feuerloescher", "Feuerlöscher", "FL", "rot"),
    ("hauptzugang", "Hauptzugang", "➜", "pfeil-voll"),
    ("nebenzugang", "Nebenzugang", "➜", "pfeil-leer"),
    ("stiege", "Stiege", "ST", "gruen"),
    ("aufzug", "Aufzug", "AZ", "box"),
    ("gefahr_ex", "Gefahr: EX-Bereich", "EX", "dreieck"),
    ("gefahr_gas", "Gefahr: Gas", "GAS", "dreieck"),
    ("gefahr_chemie", "Gefahr: Chemie", "CHE", "dreieck"),
    ("gefahr_strom", "Gefahr: Hochspannung", "kV", "dreieck"),
    ("gefahr_pv", "Gefahr: Photovoltaik", "PV", "dreieck"),
    ("hydrant_ueberflur", "Hydrant (Überflur)", "H", "hydrant"),
    ("hydrant_unterflur", "Hydrant (Unterflur)", "UH", "hydrant"),
]


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_symbol` (
            `id`         BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`     BIGINT       NULL,
            `code`       VARCHAR(40)  NOT NULL,
            `name`       VARCHAR(120) NOT NULL,
            `stil`       VARCHAR(20)  NOT NULL DEFAULT 'box',
            `text`       VARCHAR(12)  NULL,
            `bild_pfad`  VARCHAR(255) NULL,
            `sort`       INT          NOT NULL DEFAULT 0,
            `aktiv`      TINYINT(1)   NOT NULL DEFAULT 1,
            `system`     TINYINT(1)   NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_objekt_symbol_org_code` (`org_id`, `code`),
            INDEX `ix_objekt_symbol_org_id` (`org_id`),
            CONSTRAINT `fk_objekt_symbol_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    for i, (code, name, txt, stil) in enumerate(_SEED, start=1):
        op.execute(text(f"""
            INSERT INTO `objekt_symbol` (`org_id`, `code`, `name`, `text`, `stil`, `sort`, `aktiv`, `system`)
            SELECT `id`, '{code}', '{name}', '{txt}', '{stil}', {i}, 1, 1
            FROM `fire_dept`
            WHERE NOT EXISTS (
                SELECT 1 FROM `objekt_symbol` s2
                WHERE s2.`org_id` = `fire_dept`.`id` AND s2.`code` = '{code}'
            )
        """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `objekt_symbol`"))
