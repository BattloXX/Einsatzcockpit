"""Objektdokumente: Volltext-Indexierung je Seite (Suche)

- objekt_dokument_seite.volltext (LONGTEXT) + text_quelle (pdf/ocr/none)

Revision ID: 0134
Revises: 0133
Create Date: 2026-07-06
"""
from sqlalchemy import text

from alembic import op

revision = "0134"
down_revision = "0133"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE `objekt_dokument_seite` "
        "ADD COLUMN `volltext` LONGTEXT NULL, "
        "ADD COLUMN `text_quelle` VARCHAR(10) NULL"
    ))


def downgrade() -> None:
    op.execute(text(
        "ALTER TABLE `objekt_dokument_seite` "
        "DROP COLUMN `volltext`, DROP COLUMN `text_quelle`"
    ))
