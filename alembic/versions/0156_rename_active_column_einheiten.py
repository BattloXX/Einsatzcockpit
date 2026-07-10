"""Spalte "Tatsächlich im Einsatz" in "Einheiten" umbenennen

Reine Daten-Migration: aktualisiert nur Spalten mit dem exakten alten Standard-Titel,
bereits individuell umbenannte Spalten bleiben unangetastet.

Revision ID: 0156
Revises: 0155
Create Date: 2026-07-10
"""
from alembic import op
from sqlalchemy import text

revision = "0156"
down_revision = "0155"
branch_labels = None
depends_on = None

OLD_TITLE = "Tatsächlich im Einsatz"
NEW_TITLE = "Einheiten"


def upgrade() -> None:
    op.execute(text("""
        UPDATE `incident_column` SET `title` = :new_title
        WHERE `code` = 'active' AND `title` = :old_title
    """).bindparams(new_title=NEW_TITLE, old_title=OLD_TITLE))


def downgrade() -> None:
    op.execute(text("""
        UPDATE `incident_column` SET `title` = :old_title
        WHERE `code` = 'active' AND `title` = :new_title
    """).bindparams(new_title=NEW_TITLE, old_title=OLD_TITLE))
