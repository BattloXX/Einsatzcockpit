"""Hydranten-/Löschwasser-Layer (OSM/OSMHydrant)

- org_settings.hydrant_layer_enabled: Org-Schalter (effektiv = Settings.HYDRANT_ENABLED AND Org)
- incident.hydranten_json / incident.hydranten_stand: Offline-Momentaufnahme der
  Löschwasser-Entnahmestellen am Einsatzort (Fallback wenn Overpass nicht erreichbar)

Revision ID: 0131
Revises: 0130
Create Date: 2026-07-05
"""
from sqlalchemy import text

from alembic import op

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `hydrant_layer_enabled` TINYINT(1) NOT NULL DEFAULT 1
    """))
    op.execute(text("""
        ALTER TABLE `incident`
        ADD COLUMN IF NOT EXISTS `hydranten_json` TEXT NULL
    """))
    op.execute(text("""
        ALTER TABLE `incident`
        ADD COLUMN IF NOT EXISTS `hydranten_stand` DATETIME NULL
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `incident` DROP COLUMN IF EXISTS `hydranten_stand`"))
    op.execute(text("ALTER TABLE `incident` DROP COLUMN IF EXISTS `hydranten_json`"))
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `hydrant_layer_enabled`"))
