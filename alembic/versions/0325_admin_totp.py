"""Optionale Systemadministrator-2FA, standardmaessig aus.

Revision ID: 0325
Revises: 0324
"""
import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision = "0325"
down_revision = "0324"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE `user` ADD COLUMN `session_epoch` INT NOT NULL DEFAULT 0"))
    op.execute(text("ALTER TABLE `user` ADD COLUMN `admin_mcp_access` BOOL NOT NULL DEFAULT false"))
    op.create_table(
        "admin_totp",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "admin_recovery_code",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_admin_recovery_code_user_id", "admin_recovery_code", ["user_id"])


def downgrade() -> None:
    op.drop_table("admin_recovery_code")
    op.drop_table("admin_totp")
    op.execute(text("ALTER TABLE `user` DROP COLUMN `admin_mcp_access`"))
    op.execute(text("ALTER TABLE `user` DROP COLUMN `session_epoch`"))
