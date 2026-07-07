"""Wasserstellen-/Löschwasser-Stammdaten je Org

Manuell gepflegte Löschwasser-Entnahmestellen (Import aus EUS/GIS-CSV oder
Handpflege). Ergänzt den live-OSM-Hydranten-Layer (0131): eigene Stammdaten haben
auf der Einsatzinfo-Karte Vorrang, OSM zeigt nur noch Nachbarorte.

Revision ID: 0140
Revises: 0139
Create Date: 2026-07-07
"""
from sqlalchemy import text

from alembic import op

revision = "0140"
down_revision = "0139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `wasserstelle` (
            `id`                 BIGINT NOT NULL AUTO_INCREMENT,
            `org_id`             BIGINT NOT NULL,
            `bezeichnung`        VARCHAR(250) NOT NULL,
            `typ`                VARCHAR(20) NOT NULL DEFAULT 'ueberflur',
            `lat`                DOUBLE NULL,
            `lng`                DOUBLE NULL,
            `hinweis`            TEXT NULL,
            `ergiebigkeit_l_min` INT NULL,
            `quelle`             VARCHAR(20) NOT NULL DEFAULT 'manuell',
            `import_key`         VARCHAR(64) NULL,
            `aktiv`              TINYINT(1) NOT NULL DEFAULT 1,
            `erstellt_am`        DATETIME NULL,
            `aktualisiert_am`    DATETIME NULL,
            `erstellt_von_id`    BIGINT NULL,
            `aktualisiert_von_id` BIGINT NULL,
            PRIMARY KEY (`id`),
            KEY `ix_wasserstelle_org_typ` (`org_id`, `typ`),
            KEY `ix_wasserstelle_org_geo` (`org_id`, `lat`, `lng`),
            KEY `ix_wasserstelle_org_import` (`org_id`, `import_key`),
            CONSTRAINT `fk_wasserstelle_erstellt_von`
                FOREIGN KEY (`erstellt_von_id`) REFERENCES `user` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_wasserstelle_aktualisiert_von`
                FOREIGN KEY (`aktualisiert_von_id`) REFERENCES `user` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `wasserstelle`"))
