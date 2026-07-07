"""ECPG: print_job (Druckaufträge, idempotent)

Revision ID: 0142
Revises: 0141
Create Date: 2026-07-07
"""
from sqlalchemy import text

from alembic import op

revision = "0142"
down_revision = "0141"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `print_job` (
            `id`              BIGINT NOT NULL AUTO_INCREMENT,
            `org_id`          BIGINT NULL,
            `gateway_id`      BIGINT NOT NULL,
            `printer_id`      BIGINT NULL,
            `source`          VARCHAR(20) NOT NULL DEFAULT 'manual',
            `rule_id`         BIGINT NULL,
            `incident_id`     BIGINT NULL,
            `gsl_id`          BIGINT NULL,
            `objekt_id`       BIGINT NULL,
            `document_type`   VARCHAR(40) NOT NULL,
            `artifact_ref`    VARCHAR(120) NULL,
            `options`         JSON NULL,
            `status`          VARCHAR(20) NOT NULL DEFAULT 'queued',
            `idempotency_key` VARCHAR(120) NOT NULL,
            `attempts`        INT NOT NULL DEFAULT 0,
            `error`           VARCHAR(500) NULL,
            `created_by_id`   BIGINT NULL,
            `erstellt_am`     DATETIME NULL,
            `aktualisiert_am` DATETIME NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_print_job_idempotency` (`idempotency_key`),
            KEY `ix_print_job_org_incident` (`org_id`, `incident_id`),
            KEY `ix_print_job_org_gateway_status` (`org_id`, `gateway_id`, `status`),
            CONSTRAINT `fk_print_job_gateway`
                FOREIGN KEY (`gateway_id`) REFERENCES `gateway` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_print_job_printer`
                FOREIGN KEY (`printer_id`) REFERENCES `printer` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_print_job_incident`
                FOREIGN KEY (`incident_id`) REFERENCES `incident` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_print_job_gsl`
                FOREIGN KEY (`gsl_id`) REFERENCES `major_incident` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_print_job_objekt`
                FOREIGN KEY (`objekt_id`) REFERENCES `objekt` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_print_job_user`
                FOREIGN KEY (`created_by_id`) REFERENCES `user` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_print_job_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `print_job`"))
