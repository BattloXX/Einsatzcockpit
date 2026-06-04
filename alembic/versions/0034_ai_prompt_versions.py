"""KI-Prompt-Versionsverlauf

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-04 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_prompt_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("prompt_key", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("variable_part", sa.Text(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_username", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prompt_key", "version", name="uq_ai_prompt_version"),
    )
    op.create_index("ix_ai_prompt_versions_prompt_key", "ai_prompt_versions", ["prompt_key"])


def downgrade() -> None:
    op.drop_index("ix_ai_prompt_versions_prompt_key", table_name="ai_prompt_versions")
    op.drop_table("ai_prompt_versions")
