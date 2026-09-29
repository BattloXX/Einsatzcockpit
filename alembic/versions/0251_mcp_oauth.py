"""MCP OAuth tables and module flag.

Revision ID: 0251
Revises: 0250
"""

import sqlalchemy as sa

from alembic import op

revision = "0251"
down_revision = "0250"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_settings", sa.Column("mcp_modul_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "mcp_oauth_client",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("client_id", sa.String(255), nullable=False, unique=True),
        sa.Column("client_secret_hash", sa.String(64)),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "mcp_oauth_code",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE")),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE")),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(255), nullable=False),
        sa.Column("scopes", sa.String(500), nullable=False),
        sa.Column("state", sa.String(1000)),
        sa.Column("resource", sa.String(1000)),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime()),
    )
    op.create_index("ix_mcp_oauth_code_code_hash", "mcp_oauth_code", ["code_hash"])
    op.create_table(
        "mcp_oauth_token",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_type", sa.String(10), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scopes", sa.String(500), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("last_used_at", sa.DateTime()),
        sa.Column("family_id", sa.String(64), nullable=False),
    )
    op.create_index("ix_mcp_oauth_token_token_hash", "mcp_oauth_token", ["token_hash"])
    op.create_index("ix_mcp_oauth_token_family_id", "mcp_oauth_token", ["family_id"])


def downgrade() -> None:
    op.drop_table("mcp_oauth_token")
    op.drop_table("mcp_oauth_code")
    op.drop_table("mcp_oauth_client")
    op.drop_column("org_settings", "mcp_modul_aktiv")
