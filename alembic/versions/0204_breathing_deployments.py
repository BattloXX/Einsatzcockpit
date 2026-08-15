"""Atemschutz Phase 4: mehrere Einsatzzyklen je Trupp.

Revision ID: 0204
Revises: 0203
"""
import sqlalchemy as sa

from alembic import op

revision = "0204"
down_revision = "0203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "breathing_deployment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("troop_id", sa.BigInteger(), nullable=False),
        sa.Column("lfd_nr", sa.Integer(), nullable=False),
        sa.Column("entry_at", sa.DateTime(), nullable=True),
        sa.Column("withdraw_at", sa.DateTime(), nullable=True),
        sa.Column("back_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("planned_duration_min", sa.Integer(), nullable=True),
        sa.Column("bottle_preset", sa.String(20), nullable=True),
        sa.Column("task_text", sa.String(300), nullable=True),
        sa.Column("location_text", sa.String(200), nullable=True),
        sa.Column("start_press_avg", sa.Float(), nullable=True),
        sa.Column("withdraw_press_calc", sa.Float(), nullable=True),
        sa.Column("warn_one_third_acked_at", sa.DateTime(), nullable=True),
        sa.Column("warn_two_third_acked_at", sa.DateTime(), nullable=True),
        sa.Column("warn_max_time_acked_at", sa.DateTime(), nullable=True),
        sa.Column("warn_withdraw_acked_at", sa.DateTime(), nullable=True),
        sa.Column("warn_withdraw_acked_press", sa.Float(), nullable=True),
        sa.Column("readiness_override_reason", sa.String(500), nullable=True),
        sa.Column("readiness_override_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("readiness_override_at", sa.DateTime(), nullable=True),
        sa.Column("escalated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["troop_id"], ["breathing_troop.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["readiness_override_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("troop_member") as batch:
        batch.add_column(sa.Column("deployment_id", sa.BigInteger(), nullable=True))
        batch.create_foreign_key(
            "fk_troop_member_deployment_id",
            "breathing_deployment", ["deployment_id"], ["id"], ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("troop_member") as batch:
        batch.drop_constraint("fk_troop_member_deployment_id", type_="foreignkey")
        batch.drop_column("deployment_id")
    op.drop_table("breathing_deployment")
