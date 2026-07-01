"""IncidentColumn: Abschnittsleiter je Lane (Mitglied oder Freitext)

Revision ID: 0111
Revises: 0110
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incident_column") as batch:
        batch.add_column(
            sa.Column("section_leader_member_id", sa.BigInteger(), nullable=True),
        )
        batch.add_column(
            sa.Column("section_leader_name", sa.String(200), nullable=True),
        )
        batch.create_foreign_key(
            "fk_incident_column_section_leader_member",
            "member", ["section_leader_member_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("incident_column") as batch:
        batch.drop_constraint("fk_incident_column_section_leader_member", type_="foreignkey")
        batch.drop_column("section_leader_name")
        batch.drop_column("section_leader_member_id")
