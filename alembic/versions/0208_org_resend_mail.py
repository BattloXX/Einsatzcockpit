"""Resend-Mailkonfiguration je Organisation.

Revision ID: 0208
Revises: 0207
Create Date: 2026-08-21
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "0208"
down_revision = "0207"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "org_resend_mail_config" not in sa_inspect(bind).get_table_names():
        op.create_table(
            "org_resend_mail_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(),
                      sa.ForeignKey("fire_dept.id", ondelete="CASCADE"),
                      unique=True, nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("api_key_enc", sa.Text(), nullable=True),
            sa.Column("from_addr", sa.String(320), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "org_resend_mail_config" in sa_inspect(bind).get_table_names():
        op.drop_table("org_resend_mail_config")
