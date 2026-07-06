"""Objektverwaltung: pflegbare Auswahllisten (Kontaktarten, Dokumentarten, Piktogramme)

- objekt_auswahl: generische Lookup-Tabelle je Org mit Diskriminator `typ`
- Seeds je bestehender Org aus den bisherigen Modul-Konstanten (system=1)

Revision ID: 0132
Revises: 0131
Create Date: 2026-07-06
"""
from sqlalchemy import text

from alembic import op

revision = "0132"
down_revision = "0131"
branch_labels = None
depends_on = None

# typ -> [(code, icon|None, name)] — identisch zu STANDARD_AUSWAHL im objekt_service
_SEED: dict[str, list[tuple[str, str | None, str]]] = {
    "kontaktart": [
        ("brandschutzbeauftragter", None, "Brandschutzbeauftragter"),
        ("betreiber", None, "Betreiber"),
        ("hausverwaltung", None, "Hausverwaltung"),
        ("schluesseltraeger", None, "Schlüsselträger"),
        ("sonstig", None, "Sonstiger Kontakt"),
    ],
    "dokumentart": [
        ("bma_datenblatt", None, "BMA Datenblatt"),
        ("bma_melderplan", None, "BMA Melderplan"),
        ("brandschutzplan", None, "Brandschutzplan"),
        ("gefahrgutdatenblatt", None, "Gefahrgutdatenblatt"),
        ("lageplan", None, "Lageplan"),
        ("objektinformation", None, "Objektinformation"),
    ],
    "piktogramm": [
        ("ex", "💥", "EX-Bereich"),
        ("gas", "🔥", "Gas"),
        ("chemie", "🧪", "Chemie / Gefahrstoff"),
        ("hochspannung", "⚡", "Hochspannung"),
        ("pv", "☀️", "Photovoltaik"),
        ("nh3", "❄️", "Ammoniak (NH3)"),
        ("brandlast", "🔥", "Hohe Brandlast"),
        ("sonstig", "⚠️", "Sonstige Gefahr"),
    ],
}


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_auswahl` (
            `id`      BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`  BIGINT       NULL,
            `typ`     VARCHAR(30)  NOT NULL,
            `code`    VARCHAR(40)  NOT NULL,
            `name`    VARCHAR(120) NOT NULL,
            `icon`    VARCHAR(40)  NULL,
            `sort`    INT          NOT NULL DEFAULT 0,
            `aktiv`   TINYINT(1)   NOT NULL DEFAULT 1,
            `system`  TINYINT(1)   NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_objekt_auswahl_org_typ_code` (`org_id`, `typ`, `code`),
            INDEX `ix_objekt_auswahl_org_id` (`org_id`),
            INDEX `ix_objekt_auswahl_org_typ` (`org_id`, `typ`),
            CONSTRAINT `fk_objekt_auswahl_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # Seeds je bestehender Org (system=1). Idempotent per NOT EXISTS.
    for typ, eintraege in _SEED.items():
        for i, (code, icon, name) in enumerate(eintraege, start=1):
            icon_sql = "NULL" if icon is None else f"'{icon}'"
            op.execute(text(f"""
                INSERT INTO `objekt_auswahl` (`org_id`, `typ`, `code`, `name`, `icon`, `sort`, `aktiv`, `system`)
                SELECT `id`, '{typ}', '{code}', '{name}', {icon_sql}, {i}, 1, 1
                FROM `fire_dept`
                WHERE NOT EXISTS (
                    SELECT 1 FROM `objekt_auswahl` a2
                    WHERE a2.`org_id` = `fire_dept`.`id`
                      AND a2.`typ` = '{typ}' AND a2.`code` = '{code}'
                )
            """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `objekt_auswahl`"))
