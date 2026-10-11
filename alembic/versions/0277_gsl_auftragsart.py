"""Auftragsart für GSL-Dispatches.

Revision ID: 0277
Revises: 0276
"""
import sqlalchemy as sa

from alembic import op

revision = "0277"
down_revision = "0276"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("einheit_site_dispatch") as batch:
        batch.add_column(
            sa.Column("auftragsart", sa.String(12), nullable=False, server_default=sa.text("'abarbeitung'"))
        )
    # Auch MariaDB setzt den Default bei ADD COLUMN; das UPDATE dokumentiert und
    # sichert den Bestand explizit ab.
    op.execute(sa.text("UPDATE einheit_site_dispatch SET auftragsart = 'abarbeitung' WHERE auftragsart IS NULL"))


def downgrade() -> None:
    with op.batch_alter_table("einheit_site_dispatch") as batch:
        batch.drop_column("auftragsart")
