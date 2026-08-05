"""Drosselzustand fuer Einsatz-Live-Web-Pushes.

Revision ID: 0193
Revises: 0192
"""
import sqlalchemy as sa

from alembic import op

revision = "0193"
down_revision = "0192"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "incident",
        sa.Column("live_push_phase", sa.Integer(), nullable=True),
    )
    op.add_column(
        "incident",
        sa.Column("live_push_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("incident", "live_push_at")
    op.drop_column("incident", "live_push_phase")
