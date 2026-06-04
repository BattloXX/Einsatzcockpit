"""KI-Verlaufsberichtsentwurf auf Incident

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-04 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incident", sa.Column("ai_report_draft", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("incident", "ai_report_draft")
