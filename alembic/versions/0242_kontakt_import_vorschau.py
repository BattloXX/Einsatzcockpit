"""Persist contact import previews.

Revision ID: 0242
Revises: 0241
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0242"
down_revision = "0241"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kontakt_import_vorschau",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("zeilen_json", sa.Text(), nullable=False),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_kontakt_import_vorschau_org_id", "kontakt_import_vorschau", ["org_id"])
    op.create_index("ix_kontakt_import_vorschau_org_user", "kontakt_import_vorschau", ["org_id", "user_id"])


def downgrade() -> None:
    op.drop_table("kontakt_import_vorschau")
