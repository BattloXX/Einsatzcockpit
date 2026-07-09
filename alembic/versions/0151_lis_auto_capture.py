"""LIS/IPR: automatischer Rohdaten-Capture-Start bei neuem Einsatz

- org_lis_config.auto_capture_on_new_operation: wenn gesetzt, startet
  start_capture_for_org() automatisch eine 120-Minuten-Aufzeichnung, sobald
  lis_sync.py einen neuen Einsatz aus dieser LIS-Anbindung anlegt (Diagnose,
  nur system_admin, siehe ui_lis.py).

Revision ID: 0151
Revises: 0150
Create Date: 2026-07-09
"""
from alembic import op
from sqlalchemy import text

revision = "0151"
down_revision = "0150"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_lis_config`
        ADD COLUMN IF NOT EXISTS `auto_capture_on_new_operation` TINYINT(1) NOT NULL DEFAULT 0
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_lis_config` DROP COLUMN IF EXISTS `auto_capture_on_new_operation`"))
