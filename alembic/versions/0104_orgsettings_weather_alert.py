"""OrgSettings: Wetterwarnung-Kanäle und Bodensee-Override

Revision ID: 0104
Revises: 0103
Create Date: 2026-06-25
"""
from alembic import op
from sqlalchemy import text

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `weather_alert_mail`              VARCHAR(255)  NULL,
        ADD COLUMN IF NOT EXISTS `weather_alert_teams_webhook_url` VARCHAR(1000) NULL,
        ADD COLUMN IF NOT EXISTS `bodensee_temp_override_c`        DOUBLE        NULL,
        ADD COLUMN IF NOT EXISTS `bodensee_temp_override_at`       DATETIME      NULL
    """))


def downgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_settings`
        DROP COLUMN IF EXISTS `weather_alert_mail`,
        DROP COLUMN IF EXISTS `weather_alert_teams_webhook_url`,
        DROP COLUMN IF EXISTS `bodensee_temp_override_c`,
        DROP COLUMN IF EXISTS `bodensee_temp_override_at`
    """))
