"""Zweck fuer Passwort-Reset-Tokens.

Revision ID: 0291
Revises: 0290
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0291"
down_revision = "0290"
branch_labels = None
depends_on = None


def _spalten(bind, tabelle: str) -> set[str]:
    return {spalte["name"] for spalte in sa.inspect(bind).get_columns(tabelle)}


def upgrade() -> None:
    bind = op.get_bind()
    if "zweck" not in _spalten(bind, "password_reset_token"):
        with op.batch_alter_table("password_reset_token") as batch:
            batch.add_column(sa.Column("zweck", sa.String(length=16), nullable=False, server_default="reset"))


def downgrade() -> None:
    bind = op.get_bind()
    if "zweck" in _spalten(bind, "password_reset_token"):
        with op.batch_alter_table("password_reset_token") as batch:
            batch.drop_column("zweck")
