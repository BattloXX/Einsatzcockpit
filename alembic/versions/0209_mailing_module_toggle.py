"""Mailing-Modul Org-Feature-Flag.

Revision ID: 0209
Revises: 0208
"""

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "0209"
down_revision = "0208"
branch_labels = None
depends_on = None


def _has(bind) -> bool:
    return any(c["name"] == "mailing_module_enabled" for c in sa_inspect(bind).get_columns("org_settings"))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has(bind):
        op.add_column(
            "org_settings", sa.Column("mailing_module_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has(bind):
        op.drop_column("org_settings", "mailing_module_enabled")
