"""Add contact note to object care order.

Revision ID: 0249
Revises: 0248
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0249"
down_revision = "0248"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("objekt_pflegeauftrag", sa.Column("kontakt_notiz", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("objekt_pflegeauftrag", "kontakt_notiz")
