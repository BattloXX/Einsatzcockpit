"""Atemschutz Phase 3: Eskalation bei Funkstille.

Revision ID: 0203
Revises: 0202
"""
import sqlalchemy as sa

from alembic import op

revision = "0203"
down_revision = "0202"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("breathing_troop") as batch:
        batch.add_column(sa.Column("escalated_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("fire_dept") as batch:
        batch.add_column(sa.Column(
            "escalation_grace_min", sa.Integer(), nullable=False, server_default="3"
        ))


def downgrade() -> None:
    with op.batch_alter_table("fire_dept") as batch:
        batch.drop_column("escalation_grace_min")
    with op.batch_alter_table("breathing_troop") as batch:
        batch.drop_column("escalated_at")
