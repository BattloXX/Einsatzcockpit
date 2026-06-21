"""Geraeteverleih – Notizfeld an VerleihAusleihe

Revision ID: 0092
Revises: 0091
Create Date: 2026-06-21
"""

from alembic import op
from sqlalchemy import text

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        "ALTER TABLE verleih_ausleihe ADD COLUMN IF NOT EXISTS notizen TEXT NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        "ALTER TABLE verleih_ausleihe DROP COLUMN IF EXISTS notizen"
    ))
