"""DIBOS-Wachenstatus je Einsatz speichern.

Revision ID: 0199
Revises: 0198
"""
import sqlalchemy as sa

from alembic import op

revision = "0199"
down_revision = "0198"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_dibos_config", sa.Column("wache_unid", sa.String(50), nullable=True))
    op.create_table(
        "incident_wache_status",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("wache_unid", sa.String(50), nullable=False),
        sa.Column("wache_name", sa.String(120), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_text_raw", sa.String(20), nullable=True),
        sa.Column("status_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incident.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "wache_unid", name="uq_incident_wache_unid"),
    )
    op.create_index("ix_incident_wache_status_incident_id", "incident_wache_status", ["incident_id"])


def downgrade() -> None:
    op.drop_index("ix_incident_wache_status_incident_id", table_name="incident_wache_status")
    op.drop_table("incident_wache_status")
    op.drop_column("org_dibos_config", "wache_unid")
