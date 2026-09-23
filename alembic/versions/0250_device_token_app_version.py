"""Add app version to device token.

Revision ID: 0250
Revises: 0249
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0250"
down_revision = "0249"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("device_token", sa.Column("app_version", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("device_token", "app_version")
