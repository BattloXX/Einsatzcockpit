"""Nachschlagewerke: rettungsdatenblatt_cache (globaler Fahrzeug-Rettungskarten-Cache)

Geteiltes Nachschlagewerk ohne Org-Bezug: on-demand geladene Rettungsdatenblaetter
werden hier zwischengespeichert (Metadaten + Dateipfad), damit sie nach dem ersten
Abruf offline (SW cache-first) verfuegbar sind. Datei liegt im Dateisystem.

Revision ID: 0165
Revises: 0164
Create Date: 2026-07-17
"""
from sqlalchemy import text

from alembic import op

revision = "0165"
down_revision = "0164"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `rettungsdatenblatt_cache` (
            `id`           INTEGER NOT NULL AUTO_INCREMENT,
            `hersteller`   VARCHAR(100) NOT NULL,
            `modell`       VARCHAR(150) NOT NULL,
            `baujahr_von`  INT NULL,
            `baujahr_bis`  INT NULL,
            `kraftstoff`   VARCHAR(40) NULL,
            `quelle`       VARCHAR(500) NULL,
            `pfad`         VARCHAR(300) NULL,
            `bytes`        INT NOT NULL DEFAULT 0,
            `sha256`       VARCHAR(64) NULL,
            `abgerufen_am` DATETIME NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_rdb_hersteller_modell_baujahr` (`hersteller`, `modell`, `baujahr_von`),
            KEY `ix_rdb_hersteller` (`hersteller`),
            KEY `ix_rdb_modell` (`modell`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `rettungsdatenblatt_cache`"))
