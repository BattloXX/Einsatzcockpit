"""Objekt-Dokumentseite: persistente Anzeige-Drehung

- objekt_dokument_seite.rotation (0/90/180/270), im Bearbeitungsmodus gesetzt

Revision ID: 0138
Revises: 0137
Create Date: 2026-07-06
"""
from sqlalchemy import text

from alembic import op

revision = "0138"
down_revision = "0137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE `objekt_dokument_seite` "
        "ADD COLUMN IF NOT EXISTS `rotation` INT NOT NULL DEFAULT 0"
    ))


def downgrade() -> None:
    op.execute(text(
        "ALTER TABLE `objekt_dokument_seite` DROP COLUMN IF EXISTS `rotation`"
    ))
