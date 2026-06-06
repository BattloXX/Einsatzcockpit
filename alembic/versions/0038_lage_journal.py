"""Lage-Journal (Einsatztagebuch) fuer Großschadenslagen

Revision ID: 0038
Revises: 0037
Create Date: 2026-06-06 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lage_journal_entry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "major_incident_id",
            sa.Integer(),
            sa.ForeignKey("major_incident.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="sonstiges"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("author_name", sa.String(120), nullable=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("lage_journal_entry")
