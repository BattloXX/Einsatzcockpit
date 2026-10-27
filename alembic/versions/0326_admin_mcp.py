"""Getrennte OAuth-Tabellen fuer den lesenden Admin-MCP.

Revision ID: 0326
Revises: 0325
"""
import sqlalchemy as sa

from alembic import op

revision = "0326"
down_revision = "0325"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("admin_mcp_oauth_client",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("client_id", sa.String(255), nullable=False, unique=True),
        sa.Column("client_secret_hash", sa.String(64)),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("admin_mcp_oauth_code",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE")),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(255), nullable=False),
        sa.Column("scopes", sa.String(500), nullable=False),
        sa.Column("state", sa.String(1000)), sa.Column("resource", sa.String(1000)),
        sa.Column("expires_at", sa.DateTime(), nullable=False), sa.Column("consumed_at", sa.DateTime()),
        sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mfa_at", sa.DateTime()))
    op.create_table("admin_mcp_oauth_token",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_type", sa.String(10), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scopes", sa.String(500), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()), sa.Column("last_used_at", sa.DateTime()),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("session_epoch", sa.Integer(), nullable=False), sa.Column("mfa_at", sa.DateTime()),
        sa.Column("family_expires_at", sa.DateTime(), nullable=False))
    for table, columns in (("code", ("code_hash", "client_id")),
                           ("token", ("token_hash", "client_id", "family_id"))):
        for column in columns:
            name = "admin_mcp_oauth_" + table
            op.create_index("ix_" + name + "_" + column, name, [column])


def downgrade() -> None:
    for table in ("token", "code", "client"):
        op.drop_table("admin_mcp_oauth_" + table)
