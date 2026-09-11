"""Add organisation toggle for the contacts module.

Revision ID: 0239
Revises: 0238
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0239"
down_revision = "0238"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch_op:
        batch_op.add_column(
            sa.Column("kontakte_module_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch_op:
        batch_op.drop_column("kontakte_module_enabled")
