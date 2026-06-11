"""Multi-tenancy PR 3 (Expand): org_id auf Vorlagen-Tabellen; ext-key Unique vorbereiten

Revision ID: 0048
Revises: 0047
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. org_id (nullable) auf die 4 Vorlagen-Tabellen hinzufügen
    for table in ["task_suggestion", "message_suggestion", "lage_hint", "default_message"]:
        conn.execute(text(
            f"ALTER TABLE `{table}`"
            f"  ADD COLUMN `org_id` INT NULL DEFAULT NULL,"
            f"  ADD INDEX `ix_{table}_org_id` (`org_id`)"
        ))

    # 2. Bestehenden UNIQUE-Index auf incident.external_key entfernen
    #    (wird in 0049 durch UNIQUE(primary_org_id, external_key) ersetzt)
    try:
        conn.execute(text("ALTER TABLE `incident` DROP INDEX `external_key`"))
    except Exception:
        pass
    # Alembic/SQLAlchemy benennt den Index manchmal anders
    try:
        conn.execute(text("ALTER TABLE `incident` DROP INDEX `ix_incident_external_key`"))
    except Exception:
        pass


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0048")
