"""Multi-tenancy PR 8: Org-Self-Service – Auto-Schließen je Org, Konfig-Backup

Fügt drei nullable Auto-Schließen-Felder zu org_settings hinzu:
- autoclose_enabled   TINYINT(1) NULL  (NULL = globale Fallback-Einstellung)
- autoclose_after_hours  INT NULL
- autoclose_grace_minutes INT NULL

Revision ID: 0055
Revises: 0054
Create Date: 2026-06-11 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    conn.execute(text("""
        ALTER TABLE org_settings
        ADD COLUMN autoclose_enabled      TINYINT(1) NULL DEFAULT NULL,
        ADD COLUMN autoclose_after_hours  INT        NULL DEFAULT NULL,
        ADD COLUMN autoclose_grace_minutes INT       NULL DEFAULT NULL
    """))


def downgrade():
    conn = op.get_bind()

    conn.execute(text("ALTER TABLE org_settings DROP COLUMN autoclose_grace_minutes"))
    conn.execute(text("ALTER TABLE org_settings DROP COLUMN autoclose_after_hours"))
    conn.execute(text("ALTER TABLE org_settings DROP COLUMN autoclose_enabled"))
