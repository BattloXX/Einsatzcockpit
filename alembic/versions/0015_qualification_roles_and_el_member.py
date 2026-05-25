"""Qualifikations-Rollen (EL/GK) und Einsatzleiter-Mitglied am Einsatz

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-25 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("qualification", sa.Column(
        "is_einsatzleiter", sa.Boolean(), nullable=False, server_default="0"
    ))
    op.add_column("qualification", sa.Column(
        "is_gruppenkommandant", sa.Boolean(), nullable=False, server_default="0"
    ))
    op.add_column("incident", sa.Column(
        "incident_leader_member_id", sa.BigInteger(), nullable=True
    ))
    op.create_foreign_key(
        "fk_incident_leader_member",
        "incident", "member",
        ["incident_leader_member_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_incident_leader_member", "incident", type_="foreignkey")
    op.drop_column("incident", "incident_leader_member_id")
    op.drop_column("qualification", "is_gruppenkommandant")
    op.drop_column("qualification", "is_einsatzleiter")
