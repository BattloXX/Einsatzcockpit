"""ECPG: print_rule (Automatik-Druckregeln, Phase 4) + FK print_job.rule_id

Revision ID: 0144
Revises: 0143
Create Date: 2026-07-07
"""
from sqlalchemy import text

from alembic import op

revision = "0144"
down_revision = "0143"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `print_rule` (
            `id`                  BIGINT NOT NULL AUTO_INCREMENT,
            `org_id`              BIGINT NULL,
            `name`                VARCHAR(150) NOT NULL,
            `aktiv`               TINYINT(1) NOT NULL DEFAULT 1,
            `trigger`             VARCHAR(30) NOT NULL,
            `filters`             JSON NULL,
            `documents`           JSON NULL,
            `objekt_elements`     JSON NULL,
            `printer_ids`         JSON NULL,
            `fallback_printer_id` BIGINT NULL,
            `options`             JSON NULL,
            `sort_order`          INT NOT NULL DEFAULT 0,
            `erstellt_am`         DATETIME NULL,
            `aktualisiert_am`     DATETIME NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_print_rule_org_name` (`org_id`, `name`),
            KEY `ix_print_rule_org_trigger_aktiv` (`org_id`, `trigger`, `aktiv`),
            CONSTRAINT `fk_print_rule_fallback_printer`
                FOREIGN KEY (`fallback_printer_id`) REFERENCES `printer` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_print_rule_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    # print_job.rule_id nachträglich verknüpfen (print_rule existiert jetzt)
    op.execute(text(
        "ALTER TABLE `print_job` "
        "ADD CONSTRAINT `fk_print_job_rule` "
        "FOREIGN KEY (`rule_id`) REFERENCES `print_rule` (`id`) ON DELETE SET NULL"
    ))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `print_job` DROP FOREIGN KEY `fk_print_job_rule`"))
    op.execute(text("DROP TABLE IF EXISTS `print_rule`"))
