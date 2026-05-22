"""add columns missing from 0001: alarm_type.label, task.created_by_user_id, message.done_at

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # alarm_type.label — model has it, 0001 migration did not include it
    op.add_column("alarm_type", sa.Column("label", sa.String(100), nullable=False,
                                          server_default=""))

    # task.created_by_user_id — model has it, 0001 migration did not include it
    op.add_column("task", sa.Column("created_by_user_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_task_created_by_user", "task", "user",
                          ["created_by_user_id"], ["id"], ondelete="SET NULL")

    # message.done_at — model has it, 0001 migration did not include it
    op.add_column("message", sa.Column("done_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "done_at")
    op.drop_constraint("fk_task_created_by_user", "task", type_="foreignkey")
    op.drop_column("task", "created_by_user_id")
    op.drop_column("alarm_type", "label")
