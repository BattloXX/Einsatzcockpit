"""Nachschlagewerke: rettungskarten_katalog (Euro-Rescue-Modellkatalog)

Suchbarer Katalog verfuegbarer Rettungskarten (Euro NCAP / CTIF "Euro Rescue"):
Metadaten (Hersteller/Modell/Baujahr/Antrieb) + direkter PDF-Link je Modell. Das
PDF selbst wird erst beim Oeffnen on-demand in rettungsdatenblatt_cache geladen.

Revision ID: 0169
Revises: 0168
Create Date: 2026-07-18
"""
from sqlalchemy import text

from alembic import op

revision = "0169"
down_revision = "0168"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `rettungskarten_katalog` (
            `id`              INTEGER NOT NULL AUTO_INCREMENT,
            `quelle_id`       VARCHAR(40) NOT NULL,
            `hersteller`      VARCHAR(100) NOT NULL,
            `modell`          VARCHAR(150) NOT NULL,
            `karosserie`      VARCHAR(60) NULL,
            `baujahr_von`     INT NULL,
            `baujahr_bis`     INT NULL,
            `tueren`          INT NULL,
            `antrieb`         VARCHAR(60) NULL,
            `pdf_url`         VARCHAR(500) NULL,
            `pdf_sprache`     VARCHAR(8) NULL,
            `bild_url`        VARCHAR(500) NULL,
            `aktualisiert_am` DATETIME NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_rkk_quelle_id` (`quelle_id`),
            KEY `ix_rkk_hersteller` (`hersteller`),
            KEY `ix_rkk_modell` (`modell`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `rettungskarten_katalog`"))
