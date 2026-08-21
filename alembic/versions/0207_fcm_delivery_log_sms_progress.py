"""FCM-Zustellprotokoll und SMS-Versandfortschritt.

Revision ID: 0207
Revises: 0206
Create Date: 2026-08-21
"""
import sqlalchemy as sa

from alembic import op

revision = "0207"
down_revision = "0206"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sms_log") as batch:
        batch.add_column(sa.Column("completed_at", sa.DateTime(), nullable=True))
    op.execute(sa.text("UPDATE sms_log SET completed_at = sent_at"))
    with op.batch_alter_table("push_log") as batch:
        batch.add_column(sa.Column("org_id", sa.BigInteger(), nullable=True))
        batch.create_foreign_key(
            "fk_push_log_org_id_fire_dept", "fire_dept", ["org_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_push_log_org_id", ["org_id"])

    op.create_table(
        "fcm_delivery_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("push_log_id", sa.BigInteger(), nullable=False),
        sa.Column("fcm_token_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["push_log_id"], ["push_log.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fcm_token_id"], ["fcm_token.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fcm_delivery_log_push_log_id", "fcm_delivery_log", ["push_log_id"])
    op.create_index("ix_fcm_delivery_log_fcm_token_id", "fcm_delivery_log", ["fcm_token_id"])
    op.create_index("ix_fcm_delivery_log_user_id", "fcm_delivery_log", ["user_id"])
    op.create_index("ix_fcm_delivery_log_sent_at", "fcm_delivery_log", ["sent_at"])


def downgrade() -> None:
    op.drop_table("fcm_delivery_log")
    with op.batch_alter_table("push_log") as batch:
        batch.drop_index("ix_push_log_org_id")
        batch.drop_constraint("fk_push_log_org_id_fire_dept", type_="foreignkey")
        batch.drop_column("org_id")
    with op.batch_alter_table("sms_log") as batch:
        batch.drop_column("completed_at")
