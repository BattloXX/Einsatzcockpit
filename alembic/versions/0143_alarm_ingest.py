"""ECPG: alarm_ingest (serieller Alarm-Ingest, Phase 3)

Revision ID: 0143
Revises: 0142
Create Date: 2026-07-07
"""
from sqlalchemy import text

from alembic import op

revision = "0143"
down_revision = "0142"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `alarm_ingest` (
            `id`           BIGINT NOT NULL AUTO_INCREMENT,
            `org_id`       BIGINT NULL,
            `gateway_id`   BIGINT NOT NULL,
            `raw_hash`     VARCHAR(64) NOT NULL,
            `raw_text`     TEXT NOT NULL,
            `charset`      VARCHAR(20) NULL,
            `parsed`       JSON NULL,
            `parse_status` VARCHAR(20) NOT NULL DEFAULT 'parsed',
            `einsatz_id`   BIGINT NULL,
            `dedup_action` VARCHAR(20) NULL,
            `received_at`  DATETIME NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_alarm_ingest_raw_hash` (`raw_hash`),
            KEY `ix_alarm_ingest_org_received` (`org_id`, `received_at`),
            CONSTRAINT `fk_alarm_ingest_gateway`
                FOREIGN KEY (`gateway_id`) REFERENCES `gateway` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_alarm_ingest_incident`
                FOREIGN KEY (`einsatz_id`) REFERENCES `incident` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_alarm_ingest_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `alarm_ingest`"))
