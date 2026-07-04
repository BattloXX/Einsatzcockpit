"""LIS/IPR: Fahrzeugstatus-Ruckschreiben + Auto-Reopen-Sperre

- org_lis_config.push_vehicle_status: Schalter fuers Zurueckschreiben von
  Fahrzeugstatus (SetOperationUnitStatus), default aus.
- incident_vehicle.lis_operation_unit_id: LIS-OperationUnit-Id je Fahrzeug,
  noetig fuer SetOperationUnitStatus.
- incident.closed_via_lis_auto / lis_auto_close_locked: unterscheidet
  automatischen LIS-Abschluss von manuellem Abschluss und sperrt erneuten
  Auto-Close nach einem Wiedereroeffnen durch LIS.

Revision ID: 0119
Revises: 0118
Create Date: 2026-07-05
"""
from alembic import op
from sqlalchemy import text

revision = "0119"
down_revision = "0118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_lis_config`
        ADD COLUMN IF NOT EXISTS `push_vehicle_status` TINYINT(1) NOT NULL DEFAULT 0
    """))
    op.execute(text("""
        ALTER TABLE `incident_vehicle`
        ADD COLUMN IF NOT EXISTS `lis_operation_unit_id` VARCHAR(64) NULL
    """))
    op.execute(text("""
        ALTER TABLE `incident`
        ADD COLUMN IF NOT EXISTS `closed_via_lis_auto` TINYINT(1) NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS `lis_auto_close_locked` TINYINT(1) NOT NULL DEFAULT 0
    """))


def downgrade() -> None:
    op.execute(text("""
        ALTER TABLE `incident`
        DROP COLUMN IF EXISTS `closed_via_lis_auto`,
        DROP COLUMN IF EXISTS `lis_auto_close_locked`
    """))
    op.execute(text("ALTER TABLE `incident_vehicle` DROP COLUMN IF EXISTS `lis_operation_unit_id`"))
    op.execute(text("ALTER TABLE `org_lis_config` DROP COLUMN IF EXISTS `push_vehicle_status`"))
