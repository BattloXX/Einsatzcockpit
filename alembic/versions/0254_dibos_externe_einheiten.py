"""DIBOS: externe Einheiten als Platzhalter (sync_external_units).

Revision ID: 0254
Revises: 0253
"""
import sqlalchemy as sa
from alembic import op

revision = "0254"
down_revision = "0253"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "sync_external_units" not in _columns("org_dibos_config"):
        with op.batch_alter_table("org_dibos_config") as batch:
            batch.add_column(sa.Column("sync_external_units", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    if "sync_external_units" in _columns("org_dibos_config"):
        with op.batch_alter_table("org_dibos_config") as batch:
            batch.drop_column("sync_external_units")
