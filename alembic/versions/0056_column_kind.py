"""PR 5: column_kind enum für IncidentColumn

Fügt column_kind VARCHAR(20) NOT NULL DEFAULT 'vehicles' zu incident_column hinzu.
Setzt den Wert für alle bestehenden Zeilen anhand ihres code-Felds.

Revision ID: 0056
Revises: 0055
Create Date: 2026-06-12 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    conn.execute(text("""
        ALTER TABLE incident_column
        ADD COLUMN column_kind VARCHAR(20) NOT NULL DEFAULT 'vehicles'
    """))

    conn.execute(text("UPDATE incident_column SET column_kind = 'tasks'    WHERE code = 'tasks'"))
    conn.execute(text("UPDATE incident_column SET column_kind = 'messages' WHERE code = 'messages'"))
    conn.execute(text("UPDATE incident_column SET column_kind = 'rescued'  WHERE code = 'rescued'"))
    conn.execute(text("UPDATE incident_column SET column_kind = 'neighbor' WHERE code = 'neighbor'"))
    conn.execute(text("UPDATE incident_column SET column_kind = 'custom'   WHERE is_fixed = 0"))


def downgrade():
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE incident_column DROP COLUMN column_kind"))
