"""Öffentliche Probeplan-URLs verschlüsselt dauerhaft kopierbar machen.

Revision ID: 0234
Revises: 0233
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0234"
down_revision = "0233"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("probe_public_token")}
    if "token_enc" not in columns:
        op.add_column("probe_public_token", sa.Column("token_enc", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("probe_public_token")}
    if "token_enc" in columns:
        op.drop_column("probe_public_token", "token_enc")
