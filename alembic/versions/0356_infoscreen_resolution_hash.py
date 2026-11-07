"""Dauerhafter Aufloesungs-Hash fuer idempotente Infoscreen-Revisionen."""

import sqlalchemy as sa

from alembic import op

revision = "0356"
down_revision = "0355"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("infoscreen_display", sa.Column("resolution_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("infoscreen_display", "resolution_hash")
