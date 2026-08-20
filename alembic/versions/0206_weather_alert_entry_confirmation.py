"""Add entry-confirmation counter to weather alert state.

Revision ID: 0206
Revises: 0205
Create Date: 2026-08-20
"""
from alembic import op
from sqlalchemy import text

revision = "0206"
down_revision = "0205"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text(
        "ALTER TABLE `weather_alert_state` "
        "ADD COLUMN `above_threshold_cycles` INT NOT NULL DEFAULT 0"
    ))


def downgrade() -> None:
    op.execute(text(
        "ALTER TABLE `weather_alert_state` DROP COLUMN `above_threshold_cycles`"
    ))
