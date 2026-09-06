"""Standardgruppen je Probeart.

Revision ID: 0236
Revises: 0235
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0236"
down_revision = "0235"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "probeart_gruppe" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "probeart_gruppe",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
            sa.Column("probeart_id", sa.BigInteger(), sa.ForeignKey("probeart.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sms_group_id", sa.BigInteger(), sa.ForeignKey("sms_group.id", ondelete="CASCADE"), nullable=False),
            sa.UniqueConstraint("org_id", "probeart_id", "sms_group_id", name="uq_probeart_gruppe"),
        )


def downgrade() -> None:
    if "probeart_gruppe" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("probeart_gruppe")
