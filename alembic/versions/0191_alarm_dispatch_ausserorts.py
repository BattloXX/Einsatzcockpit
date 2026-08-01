"""Ausserorts-Variante fuer die Ausrueckordnung.

Revision ID: 0191
Revises: 0190
"""
import sqlalchemy as sa

from alembic import op

revision = "0191"
down_revision = "0190"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "alarm_dispatch_vehicle",
        sa.Column("is_ausserorts", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("alarm_dispatch_vehicle", "is_ausserorts")
