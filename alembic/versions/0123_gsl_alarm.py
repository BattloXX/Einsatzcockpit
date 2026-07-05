"""Großschadenslage: einmaliger SMS+Teams-Sonderalarm bei Ausrufung

- org_settings.gsl_alarm_enabled: Master-Schalter, Default an.
- org_settings.gsl_alarm_text: konfigurierbarer Hinweistext (Default siehe
  gsl_notify.py), unabhaengig von der stichwortbezogenen Einsatzinfo.

Revision ID: 0123
Revises: 0122
Create Date: 2026-07-05
"""
from alembic import op
from sqlalchemy import text

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `gsl_alarm_enabled` TINYINT(1) NOT NULL DEFAULT 1
    """))
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `gsl_alarm_text` TEXT NULL
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `gsl_alarm_enabled`"))
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `gsl_alarm_text`"))
