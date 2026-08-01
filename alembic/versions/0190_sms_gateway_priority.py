"""Prioritaet fuer SMS-Gateway-Fallback ergaenzen.

Revision ID: 0190
Revises: 0189
"""
import sqlalchemy as sa

from alembic import op

revision = "0190"
down_revision = "0189"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sms_gateway_token",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    )


def downgrade():
    op.drop_column("sms_gateway_token", "priority")
