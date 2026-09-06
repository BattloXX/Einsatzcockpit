"""Ausgewählte Mitgliedergruppen je Probe speichern.

Revision ID: 0235
Revises: 0234
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0235"
down_revision = "0234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "termin_gruppe" not in tables:
        op.create_table(
            "termin_gruppe",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
            sa.Column("termin_id", sa.BigInteger(), sa.ForeignKey("termin.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sms_group_id", sa.BigInteger(), sa.ForeignKey("sms_group.id", ondelete="CASCADE"), nullable=False),
            sa.UniqueConstraint("org_id", "termin_id", "sms_group_id", name="uq_termin_gruppe"),
        )


def downgrade() -> None:
    if "termin_gruppe" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("termin_gruppe")
