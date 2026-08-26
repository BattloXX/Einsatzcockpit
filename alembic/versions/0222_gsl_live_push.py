"""GSL Live-Push-Zustand.

Revision ID: 0222
Revises: 0221
"""
from alembic import op
import sqlalchemy as sa

revision = "0222"
down_revision = "0221"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("major_incident", sa.Column("live_push_at", sa.DateTime(), nullable=True))
    op.add_column("major_incident", sa.Column("live_push_sig", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("major_incident", "live_push_sig")
    op.drop_column("major_incident", "live_push_at")
