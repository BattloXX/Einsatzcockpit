"""Temporary token-authenticated MCP uploads.

Revision ID: 0252
Revises: 0251
"""

import sqlalchemy as sa

from alembic import op

revision = "0252"
down_revision = "0251"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_upload",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("upload_id", sa.String(32), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("objekt_id", sa.BigInteger(), sa.ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dateiname", sa.String(255), nullable=False),
        sa.Column("erwartete_bytes", sa.BigInteger()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("hochgeladen_am", sa.DateTime()),
        sa.Column("pfad", sa.Text()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("groesse_bytes", sa.BigInteger()),
        sa.Column("seitenzahl", sa.BigInteger()),
        sa.Column("uebergeben_am", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mcp_upload_upload_id", "mcp_upload", ["upload_id"])


def downgrade() -> None:
    op.drop_table("mcp_upload")
