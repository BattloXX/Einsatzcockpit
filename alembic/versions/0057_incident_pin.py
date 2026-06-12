"""PR 8: access_pin_hash für Gäste-PIN-Zugang am Einsatz

Revision ID: 0057
Revises: 0056
Create Date: 2026-06-12 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE incident
        ADD COLUMN access_pin_hash VARCHAR(120) NULL DEFAULT NULL
    """))


def downgrade():
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE incident DROP COLUMN access_pin_hash"))
