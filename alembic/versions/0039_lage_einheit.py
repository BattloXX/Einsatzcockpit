"""Lage-Einheitenpool mit Gruppenkommandant fuer Ressourcenboard

Revision ID: 0039
Revises: 0038
Create Date: 2026-06-07 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lage_einheit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "lage_id",
            sa.Integer(),
            sa.ForeignKey("major_incident.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "vehicle_id",
            sa.BigInteger(),
            sa.ForeignKey("vehicle_master.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("commander_label", sa.String(120), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="verfuegbar"),
        sa.Column("is_from_org", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("added_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("lage_einheit")
