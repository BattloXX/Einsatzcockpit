"""Add append-only offline contact sync feed.

Revision ID: 0244
Revises: 0243
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0244"
down_revision = "0243"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kontakt_sync_aenderung",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entitaet", sa.String(length=20), nullable=False),
        sa.Column("entitaet_id", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.String(length=12), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_kontakt_sync_aenderung_org_id", "kontakt_sync_aenderung", ["org_id"])
    op.create_index("ix_kontakt_sync_org_id", "kontakt_sync_aenderung", ["org_id", "id"])


def downgrade() -> None:
    op.drop_table("kontakt_sync_aenderung")
