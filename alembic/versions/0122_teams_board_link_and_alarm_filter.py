"""Teams-Einsatzinfo: Einsatz-Board-Link + Einsatzstufen-Filter

- teams_alarm_config.include_board_link: Direktlink `/einsatz/{id}` in der Karte
  (analog Web-Push, login-pflichtig — siehe teams_card.py).
- alarm_type.teams_alarm_enabled: erlaubt, einzelne Stichwoerter von der
  Teams-Alarmierung auszunehmen (Default an, wie bisher ungefiltert).

Revision ID: 0122
Revises: 0121
Create Date: 2026-07-05
"""
from alembic import op
from sqlalchemy import text

revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `teams_alarm_config`
        ADD COLUMN IF NOT EXISTS `include_board_link` TINYINT(1) NOT NULL DEFAULT 1
    """))
    op.execute(text("""
        ALTER TABLE `alarm_type`
        ADD COLUMN IF NOT EXISTS `teams_alarm_enabled` TINYINT(1) NOT NULL DEFAULT 1
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `teams_alarm_config` DROP COLUMN IF EXISTS `include_board_link`"))
    op.execute(text("ALTER TABLE `alarm_type` DROP COLUMN IF EXISTS `teams_alarm_enabled`"))
