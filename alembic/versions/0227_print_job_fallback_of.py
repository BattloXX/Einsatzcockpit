"""Herkunftsmarkierung fuer Ersatz-Druckauftraege.

Revision ID: 0227
Revises: 0226
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0227"
down_revision = "0226"
branch_labels = None
depends_on = None


def _spalten(bind) -> set[str]:
    return {spalte["name"] for spalte in sa.inspect(bind).get_columns("print_job")}


def upgrade() -> None:
    bind = op.get_bind()
    if "fallback_of_job_id" in _spalten(bind):
        return
    with op.batch_alter_table("print_job") as batch:
        batch.add_column(sa.Column("fallback_of_job_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "fallback_of_job_id" not in _spalten(bind):
        return
    with op.batch_alter_table("print_job") as batch:
        batch.drop_column("fallback_of_job_id")
