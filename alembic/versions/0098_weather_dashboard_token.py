"""weather_dashboard_token – Dashboard-Infoscreen-Token je Org

Revision ID: 0098
Revises: 0097
Create Date: 2026-06-23
"""
from sqlalchemy import text

from alembic import op  # noqa: F401

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def _col_exists(conn, table: str, col: str) -> bool:
    row = conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"t": table, "c": col}).scalar()
    return bool(row)


def _index_exists(conn, table: str, index: str) -> bool:
    row = conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
    ), {"t": table, "i": index}).scalar()
    return bool(row)


def upgrade() -> None:
    conn = op.get_bind()

    if not _col_exists(conn, "org_settings", "weather_dashboard_token_hash"):
        conn.execute(text(
            "ALTER TABLE org_settings ADD COLUMN weather_dashboard_token_hash VARCHAR(64) NULL"
        ))

    if not _index_exists(conn, "org_settings", "ix_org_settings_weather_dashboard_token_hash"):
        conn.execute(text(
            "CREATE UNIQUE INDEX ix_org_settings_weather_dashboard_token_hash "
            "ON org_settings(weather_dashboard_token_hash)"
        ))


def downgrade() -> None:
    conn = op.get_bind()

    if _index_exists(conn, "org_settings", "ix_org_settings_weather_dashboard_token_hash"):
        conn.execute(text(
            "DROP INDEX ix_org_settings_weather_dashboard_token_hash ON org_settings"
        ))

    if _col_exists(conn, "org_settings", "weather_dashboard_token_hash"):
        conn.execute(text(
            "ALTER TABLE org_settings DROP COLUMN weather_dashboard_token_hash"
        ))
