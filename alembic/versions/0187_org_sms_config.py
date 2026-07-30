"""SMS-Provider-Konfiguration je Organisation.

Revision ID: 0187
Revises: 0186
Create Date: 2026-07-30
"""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from alembic import op

revision = "0187"
down_revision = "0186"
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    return table in sa_inspect(conn).get_table_names()


def _column_exists(conn, table: str, column: str) -> bool:
    if conn.dialect.name != "mysql":
        return any(c["name"] == column for c in sa_inspect(conn).get_columns(table))
    result = conn.execute(
        text(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    )
    return (result.scalar() or 0) > 0


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "org_sms_config"):
        op.create_table(
            "org_sms_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(),
                      sa.ForeignKey("fire_dept.id", ondelete="CASCADE"),
                      unique=True, nullable=False),
            sa.Column("primary_provider", sa.String(20), nullable=False, server_default="gateway"),
            sa.Column("fallback_provider", sa.String(20), nullable=True),
            sa.Column("eus_enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("eus_base_url", sa.String(300), nullable=True),
            sa.Column("eus_auth_mode", sa.String(10), nullable=False, server_default="oauth"),
            sa.Column("eus_client_id", sa.String(200), nullable=True),
            sa.Column("eus_client_secret_enc", sa.Text(), nullable=True),
            sa.Column("eus_timeout", sa.Integer(), nullable=False, server_default="15"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if not _column_exists(conn, "sms_log", "provider"):
        op.add_column("sms_log", sa.Column("provider", sa.String(40), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "sms_log", "provider"):
        op.drop_column("sms_log", "provider")
    if _table_exists(conn, "org_sms_config"):
        op.drop_table("org_sms_config")
