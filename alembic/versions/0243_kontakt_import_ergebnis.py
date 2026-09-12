"""Persist contact import results for download.

Revision ID: 0243
Revises: 0242
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0243"
down_revision = "0242"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kontakt_import_vorschau", sa.Column("ergebnis_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("kontakt_import_vorschau", "ergebnis_json")
