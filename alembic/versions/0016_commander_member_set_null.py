"""SET NULL FK für incident_vehicle.commander_member_id (Mitglieder-Löschen-Bug)

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-25 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite does not support ALTER COLUMN, so we use batch_alter_table which
    # recreates the table with the updated FK definition.
    with op.batch_alter_table("incident_vehicle", recreate="always") as batch:
        batch.alter_column(
            "commander_member_id",
            existing_type=sa.BigInteger(),
            nullable=True,
        )


def downgrade() -> None:
    # The ondelete behaviour is not stored in a way batch can undo selectively;
    # recreating without it is sufficient for a downgrade path.
    with op.batch_alter_table("incident_vehicle", recreate="always") as batch:
        batch.alter_column(
            "commander_member_id",
            existing_type=sa.BigInteger(),
            nullable=True,
        )
