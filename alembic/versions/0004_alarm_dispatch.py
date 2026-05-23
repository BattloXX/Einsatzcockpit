"""add alarm_dispatch_vehicle table for per-alarm-type dispatch order

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-23 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alarm_dispatch_vehicle",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("alarm_type_code", sa.String(10), nullable=False),
        sa.Column("vehicle_master_id", sa.BigInteger(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["alarm_type_code"], ["alarm_type.code"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vehicle_master_id"], ["vehicle_master.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alarm_type_code", "vehicle_master_id", name="uq_alarm_vehicle"),
    )
    op.create_index("ix_alarm_dispatch_alarm_type", "alarm_dispatch_vehicle", ["alarm_type_code"])


def downgrade() -> None:
    op.drop_index("ix_alarm_dispatch_alarm_type", table_name="alarm_dispatch_vehicle")
    op.drop_table("alarm_dispatch_vehicle")
