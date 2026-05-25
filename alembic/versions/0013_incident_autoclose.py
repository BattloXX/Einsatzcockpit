"""incident_autoclose: Felder für 48h-Lifecycle (Warnung + Auto-Close)

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-25 09:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incident",
        sa.Column("autoclose_warn_sent_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "incident",
        sa.Column("autoclose_keepopen_count", sa.Integer(),
                  nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("incident", "autoclose_keepopen_count")
    op.drop_column("incident", "autoclose_warn_sent_at")
