"""Drehung der Symbole auf der Objekt-Lagekarte.

Revision ID: 0324
Revises: 0323
"""
from sqlalchemy import text

from alembic import op

revision = "0324"
down_revision = "0323"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE `objekt_karten_objekt` ADD COLUMN `rotation` SMALLINT NOT NULL DEFAULT 0"))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `objekt_karten_objekt` DROP COLUMN `rotation`"))
