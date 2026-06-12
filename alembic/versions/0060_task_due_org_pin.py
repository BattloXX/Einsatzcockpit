"""Task-Erinnerungen + Org-Standard-PIN

Revision ID: 0060
Revises: 0059
Create Date: 2026-06-12
"""
from alembic import op
from sqlalchemy import text

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE task
            ADD COLUMN due_after_sec INT NULL,
            ADD COLUMN due_at DATETIME NULL,
            ADD COLUMN popup_shown TINYINT(1) NOT NULL DEFAULT 0
    """))
    conn.execute(text("""
        ALTER TABLE org_settings
            ADD COLUMN default_access_pin_hash VARCHAR(120) NULL
    """))


def downgrade():
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE task DROP COLUMN popup_shown, DROP COLUMN due_at, DROP COLUMN due_after_sec"))
    conn.execute(text("ALTER TABLE org_settings DROP COLUMN default_access_pin_hash"))
