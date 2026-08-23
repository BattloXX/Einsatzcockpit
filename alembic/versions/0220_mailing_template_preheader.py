"""Mailing-Vorlagen-Preheader.

Revision ID: 0220
Revises: 0219
"""

import sqlalchemy as sa

from alembic import op

revision = "0220"
down_revision = "0219"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mailing_template", sa.Column("preheader", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("mailing_template", "preheader")
