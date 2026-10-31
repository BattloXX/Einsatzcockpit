"""Capabilities der nativen FCM-App."""

import sqlalchemy as sa

from alembic import op

revision = "0337"
down_revision = "0336"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fcm_token", sa.Column("capabilities", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("fcm_token", "capabilities")
