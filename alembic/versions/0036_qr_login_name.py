"""author_name in incident_log and message for QR-Login display names

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incident_log", sa.Column("author_name", sa.String(120), nullable=True))
    op.add_column("message", sa.Column("author_name", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("incident_log", "author_name")
    op.drop_column("message", "author_name")
