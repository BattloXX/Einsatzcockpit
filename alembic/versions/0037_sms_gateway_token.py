"""Connection-Token für SMS-Gateway-Docker-Container

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-07 00:00:00.000000
"""
from alembic import op
from sqlalchemy import text

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `sms_gateway_token` (
            `id`           BIGINT        NOT NULL AUTO_INCREMENT,
            `label`        VARCHAR(150)  NOT NULL,
            `token_hash`   VARCHAR(64)   NOT NULL,
            `org_id`       BIGINT        NOT NULL,
            `created_at`   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `last_used_at` DATETIME      NULL,
            `revoked_at`   DATETIME      NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_sms_gateway_token_hash` (`token_hash`),
            CONSTRAINT `fk_sms_gateway_token_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade():
    op.execute(text("DROP TABLE IF EXISTS `sms_gateway_token`"))
