"""Wasserstelle: Betriebszustand (bereit/wartung/defekt)

Revision ID: 0145
Revises: 0144
Create Date: 2026-07-07
"""
from sqlalchemy import text

from alembic import op

revision = "0145"
down_revision = "0144"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE `wasserstelle` "
        "ADD COLUMN IF NOT EXISTS `status` VARCHAR(20) NOT NULL DEFAULT 'bereit'"
    ))
    # Bestehende inaktive Stellen als 'defekt' übernehmen (bisher nur aktiv=0/1).
    op.execute(text("UPDATE `wasserstelle` SET `status` = 'defekt' WHERE `aktiv` = 0"))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `wasserstelle` DROP COLUMN IF EXISTS `status`"))
