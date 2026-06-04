"""task.source – Herkunft einer Aufgabe (manual | ai_suggestion)

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-04 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
    )


def downgrade() -> None:
    op.drop_column("task", "source")
