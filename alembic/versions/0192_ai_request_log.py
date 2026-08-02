"""Persistentes Log fuer KI-Anfragen.

Revision ID: 0192
Revises: 0191
"""
import sqlalchemy as sa

from alembic import op

revision = "0192"
down_revision = "0191"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_request_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("feature", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_request_log_org_id"), "ai_request_log", ["org_id"], unique=False)
    op.create_index(op.f("ix_ai_request_log_created_at"), "ai_request_log", ["created_at"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_ai_request_log_created_at"), table_name="ai_request_log")
    op.drop_index(op.f("ix_ai_request_log_org_id"), table_name="ai_request_log")
    op.drop_table("ai_request_log")
