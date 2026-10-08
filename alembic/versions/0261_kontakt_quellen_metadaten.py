"""Store import document and date on contacts.

Revision ID: 0261
Revises: 0260
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0261"
down_revision = "0260"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kontakt", sa.Column("quellendokument", sa.String(255), nullable=True))
    op.add_column("kontakt", sa.Column("quellendatum", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("kontakt", "quellendatum")
    op.drop_column("kontakt", "quellendokument")
