"""Förderstrecke: Verknüpfung mit Major Incidents (lage_id) entfernen

Die Förderstrecke wird nur noch mit einem Einsatz (incident_id) verbunden.
Die Spalte lage_id (FK auf major_incident) + zugehöriger Fremdschlüssel werden
entfernt. Idempotent (IF-Checks über INFORMATION_SCHEMA), MariaDB-spezifisch.

Revision ID: 0175
Revises: 0174
Create Date: 2026-07-19
"""
from sqlalchemy import text

from alembic import op

revision = "0175"
down_revision = "0174"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    r = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"t": table, "c": column})
    return (r.scalar() or 0) > 0


def _drop_fk_on_column(conn, table: str, column: str) -> None:
    """Alle Fremdschlüssel auf der Spalte lösen (robust gegen abweichende Namen)."""
    r = conn.execute(text(
        "SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        " AND REFERENCED_TABLE_NAME IS NOT NULL"
    ), {"t": table, "c": column})
    for row in r:
        conn.execute(text(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{row[0]}`"))


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    if _column_exists(conn, "foerderstrecke", "lage_id"):
        _drop_fk_on_column(conn, "foerderstrecke", "lage_id")
        conn.execute(text("ALTER TABLE foerderstrecke DROP COLUMN lage_id"))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    if not _column_exists(conn, "foerderstrecke", "lage_id"):
        conn.execute(text(
            "ALTER TABLE foerderstrecke ADD COLUMN lage_id INT NULL,"
            " ADD CONSTRAINT fk_foerderstrecke_lage FOREIGN KEY (lage_id)"
            " REFERENCES major_incident (id) ON DELETE SET NULL"
        ))
