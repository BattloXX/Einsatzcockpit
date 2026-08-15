"""Atemschutz Phase 2: Sicherheitstrupp und Einsatzbereitschaft.

Revision ID: 0202
Revises: 0201
"""
import sqlalchemy as sa

from alembic import op

revision = "0202"
down_revision = "0201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("breathing_troop") as batch:
        batch.add_column(sa.Column(
            "is_sicherheitstrupp", sa.Boolean(), nullable=False, server_default=sa.false()
        ))
        batch.add_column(sa.Column("readiness_override_reason", sa.String(500), nullable=True))
        batch.add_column(sa.Column("readiness_override_by_user_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("readiness_override_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_breathing_troop_readiness_override_user",
            "user", ["readiness_override_by_user_id"], ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("breathing_troop") as batch:
        batch.drop_constraint("fk_breathing_troop_readiness_override_user", type_="foreignkey")
        batch.drop_column("readiness_override_at")
        batch.drop_column("readiness_override_by_user_id")
        batch.drop_column("readiness_override_reason")
        batch.drop_column("is_sicherheitstrupp")
