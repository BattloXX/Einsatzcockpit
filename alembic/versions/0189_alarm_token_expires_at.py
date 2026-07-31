"""Alarm-Tokens mit Ablaufdatum versehen und alte Abschluss-Widerrufe heilen.

Revision ID: 0189
Revises: 0188
"""
from sqlalchemy import text

from alembic import op

revision = "0189"
down_revision = "0188"
branch_labels = None
depends_on = None


def _column_exists(conn, name):
    return bool(conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='alarm_token' AND COLUMN_NAME=:name"
    ), {"name": name}).scalar())


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    if not _column_exists(conn, "expires_at"):
        conn.execute(text("ALTER TABLE alarm_token ADD COLUMN expires_at DATETIME NULL"))
    conn.execute(text(
        "UPDATE alarm_token SET expires_at=DATE_ADD(created_at, INTERVAL 365 DAY) "
        "WHERE expires_at IS NULL"
    ))
    conn.execute(text("UPDATE alarm_token SET revoked_at=NULL WHERE revoked_at IS NOT NULL"))


def downgrade():
    """Entfernt die Spalte; geleerte revoked_at-Werte sind nicht rekonstruierbar."""
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    if _column_exists(conn, "expires_at"):
        conn.execute(text("ALTER TABLE alarm_token DROP COLUMN expires_at"))
