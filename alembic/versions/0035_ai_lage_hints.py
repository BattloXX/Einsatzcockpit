"""KI-generierte Lage-Hinweise auf Einsatz

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-04 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incident", sa.Column("ai_lage_hints", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("incident", "ai_lage_hints")
