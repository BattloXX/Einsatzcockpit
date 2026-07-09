"""Fahrtzweck: optionale Einsatzleiter-Abfrage

Revision ID: 0148
Revises: 0147
Create Date: 2026-07-09
"""
from alembic import op
from sqlalchemy import text

revision = "0148"
down_revision = "0147"
branch_labels = None
depends_on = None


def upgrade():
    # Zweck-Flag: blendet (zusaetzlich zum Fahrzeug-Flag) das optionale Einsatzleiter-Feld ein.
    op.execute(text("""
        ALTER TABLE `fahrtzweck`
        ADD COLUMN IF NOT EXISTS `optional_einsatzleiter` TINYINT(1) NOT NULL DEFAULT 0
    """))


def downgrade():
    op.execute(text("""
        ALTER TABLE `fahrtzweck`
        DROP COLUMN IF EXISTS `optional_einsatzleiter`
    """))
