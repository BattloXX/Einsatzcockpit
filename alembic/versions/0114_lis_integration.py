"""LIS/IPR-Anbindung: OrgLisConfig, LisSyncedObject, Fahrzeug-/Einsatz-/Meldungs-Verknuepfung

Revision ID: 0114
Revises: 0113
Create Date: 2026-07-03
"""
from alembic import op
from sqlalchemy import text

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── LIS-Verbindungskonfiguration je Org ────────────────────────────────────
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `org_lis_config` (
            `id`                     INT          NOT NULL AUTO_INCREMENT,
            `org_id`                 BIGINT       NOT NULL,
            `enabled`                TINYINT(1)   NOT NULL DEFAULT 0,
            `base_url`               VARCHAR(300) NULL,
            `site`                   VARCHAR(50)  NOT NULL DEFAULT 'LIS',
            `organization_id`        VARCHAR(64)  NULL,
            `poll_interval_seconds`  INT          NOT NULL DEFAULT 30,
            `username`               VARCHAR(150) NULL,
            `password_enc`           LONGTEXT     NULL,
            `last_backfill_at`       DATETIME     NULL,
            `created_at`             DATETIME     NOT NULL,
            `updated_at`             DATETIME     NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_org_lis_config_org` (`org_id`),
            CONSTRAINT `fk_org_lis_config_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # ── Dedup-Merker fuer bereits verarbeitete LIS-Objekte (Task-Response, Dokument) ──
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `lis_synced_object` (
            `id`           BIGINT      NOT NULL AUTO_INCREMENT,
            `org_id`       BIGINT      NOT NULL,
            `obj_type`     VARCHAR(30) NOT NULL,
            `lis_id`       VARCHAR(64) NOT NULL,
            `incident_id`  BIGINT      NULL,
            `created_at`   DATETIME    NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_lis_synced_org_type_id` (`org_id`, `obj_type`, `lis_id`),
            CONSTRAINT `fk_lis_synced_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_lis_synced_incident` FOREIGN KEY (`incident_id`)
                REFERENCES `incident` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    # ── Fahrzeug-Mapping: stabile LIS-ReferenceId je Fahrzeug ──────────────────
    op.execute(text("""
        ALTER TABLE `vehicle_master`
        ADD COLUMN IF NOT EXISTS `lis_reference_id` VARCHAR(60) NULL
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_vehicle_master_lis_reference_id
        ON vehicle_master (lis_reference_id)
    """))

    # ── Einsatz-Verknuepfung mit LIS-Operation ─────────────────────────────────
    op.execute(text("""
        ALTER TABLE `incident`
        ADD COLUMN IF NOT EXISTS `lis_operation_id`     VARCHAR(64) NULL,
        ADD COLUMN IF NOT EXISTS `lis_operation_number` VARCHAR(40) NULL
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_incident_org_lis_op
        ON incident (primary_org_id, lis_operation_id)
    """))

    # ── Meldung-Verknuepfung mit LIS-Task (Dedup/Update bei erneutem Sync) ─────
    op.execute(text("""
        ALTER TABLE `message`
        ADD COLUMN IF NOT EXISTS `lis_task_id` VARCHAR(64) NULL
    """))
    op.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_message_lis_task_id
        ON message (lis_task_id)
    """))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS ix_message_lis_task_id ON message"))
    op.execute(text("ALTER TABLE `message` DROP COLUMN IF EXISTS `lis_task_id`"))

    op.execute(text("DROP INDEX IF EXISTS ix_incident_org_lis_op ON incident"))
    op.execute(text("""
        ALTER TABLE `incident`
        DROP COLUMN IF EXISTS `lis_operation_id`,
        DROP COLUMN IF EXISTS `lis_operation_number`
    """))

    op.execute(text("DROP INDEX IF EXISTS ix_vehicle_master_lis_reference_id ON vehicle_master"))
    op.execute(text("ALTER TABLE `vehicle_master` DROP COLUMN IF EXISTS `lis_reference_id`"))

    op.execute(text("DROP TABLE IF EXISTS `lis_synced_object`"))
    op.execute(text("DROP TABLE IF EXISTS `org_lis_config`"))
