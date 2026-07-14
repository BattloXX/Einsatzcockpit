"""LIS/IPR: externe Fahrzeuge fremder Organisationen übernehmen

- org_lis_config.sync_external_units: Opt-in-Schalter (Default aus) — übernimmt
  teilnehmende Fahrzeuge fremder Organisationen (z.B. andere Feuerwehren, Rotes
  Kreuz) aus GetOperationUnits als externe VehicleMaster-Platzhalter, statt sie
  wie bisher zu verwerfen (siehe lis_sync.py::_sync_vehicle_status()).
- vehicle_master.lis_auto_created: markiert automatisch aus LIS angelegte
  externe Fahrzeuge, grenzt sie von manuell in der Admin-UI gepflegten externen
  Fahrzeugen ab.

Revision ID: 0160
Revises: 0159
Create Date: 2026-07-14
"""
from alembic import op
from sqlalchemy import text

revision = "0160"
down_revision = "0159"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_lis_config`
        ADD COLUMN IF NOT EXISTS `sync_external_units` TINYINT(1) NOT NULL DEFAULT 0
    """))
    op.execute(text("""
        ALTER TABLE `vehicle_master`
        ADD COLUMN IF NOT EXISTS `lis_auto_created` TINYINT(1) NOT NULL DEFAULT 0
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_lis_config` DROP COLUMN IF EXISTS `sync_external_units`"))
    op.execute(text("ALTER TABLE `vehicle_master` DROP COLUMN IF EXISTS `lis_auto_created`"))
