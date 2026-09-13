"""Add optional source information to SMS log recipients.

Revision ID: 0241
Revises: 0240
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0241"
down_revision = "0240"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sms_log_recipient") as batch_op:
        batch_op.add_column(sa.Column("source_type", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("source_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sms_log_recipient") as batch_op:
        batch_op.drop_column("source_id")
        batch_op.drop_column("source_type")
