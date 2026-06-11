"""Multi-tenancy PR 3 (Migrate): org_id befüllen; composite unique auf external_key

Revision ID: 0049
Revises: 0048
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Bestand auf org_id = MIN(fire_dept.id) setzen
    for table in ["task_suggestion", "message_suggestion", "lage_hint", "default_message"]:
        conn.execute(text(
            f"UPDATE `{table}` SET `org_id` = (SELECT MIN(`id`) FROM `fire_dept`) WHERE `org_id` IS NULL"
        ))

    # 2. Composite UNIQUE(primary_org_id, external_key) auf incident hinzufügen
    #    NULL-Werte in beiden Spalten sind erlaubt (mehrere NULLs in UNIQUE → OK in MySQL/MariaDB)
    conn.execute(text(
        "ALTER TABLE `incident`"
        "  ADD CONSTRAINT `uq_incident_org_ext_key`"
        "    UNIQUE (`primary_org_id`, `external_key`)"
    ))


def downgrade():
    raise NotImplementedError("downgrade not supported for migration 0049")
