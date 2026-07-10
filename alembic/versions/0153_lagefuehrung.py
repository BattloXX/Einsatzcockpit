"""Lageführung-Modul (Phase 1 / MVP): einsatzbezogene Lagekarte

- org_settings.lagefuehrung_modul_aktiv: Org-Feature-Flag (Muster UAS/Objekt/Atemschutz).
- incident.lagefuehrung_fuehrer_user_id: aktueller Lageführer (Rollen-Grundgerüst).
- lagefuehrung_feature: manuell gesetzte Zeichnungen/Marker/Text auf der Lagekarte,
  mit version-Spalte (Optimistic Concurrency, Fundament für Multi-User-Editing Phase 2).
- lagefuehrung_event: append-only Ereignisprotokoll (Fundament für Chronologie/Replay).

Revision ID: 0153
Revises: 0152
Create Date: 2026-07-10
"""
from alembic import op
from sqlalchemy import text

revision = "0153"
down_revision = "0152"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `lagefuehrung_modul_aktiv` TINYINT(1) NOT NULL DEFAULT 0
    """))
    op.execute(text("""
        ALTER TABLE `incident`
        ADD COLUMN IF NOT EXISTS `lagefuehrung_fuehrer_user_id` BIGINT NULL,
        ADD CONSTRAINT `fk_incident_lagefuehrung_fuehrer`
            FOREIGN KEY (`lagefuehrung_fuehrer_user_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `lagefuehrung_feature` (
            `id`            BIGINT        NOT NULL AUTO_INCREMENT,
            `org_id`        BIGINT        NULL,
            `incident_id`   BIGINT        NOT NULL,
            `typ`           VARCHAR(30)   NOT NULL,
            `zeichen_key`   VARCHAR(64)   NULL,
            `geometry`      TEXT          NOT NULL,
            `rotation`      SMALLINT      NOT NULL DEFAULT 0,
            `scale`         DECIMAL(4,2)  NOT NULL DEFAULT 1.0,
            `label`         VARCHAR(255)  NULL,
            `props`         TEXT          NULL,
            `layer_gruppe`  VARCHAR(32)   NOT NULL DEFAULT 'zeichnung',
            `version`       INT           NOT NULL DEFAULT 1,
            `created_by`    BIGINT        NULL,
            `created_at`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at`    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `deleted_at`    DATETIME      NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_lagefuehrung_feature_org_incident` (`org_id`, `incident_id`),
            CONSTRAINT `fk_lagefuehrung_feature_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_lagefuehrung_feature_incident`
                FOREIGN KEY (`incident_id`) REFERENCES `incident`(`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_lagefuehrung_feature_user`
                FOREIGN KEY (`created_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `lagefuehrung_event` (
            `id`          BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`      BIGINT       NULL,
            `incident_id` BIGINT       NOT NULL,
            `ts`          DATETIME(3)  NOT NULL,
            `user_id`     BIGINT       NULL,
            `event_typ`   VARCHAR(48)  NOT NULL,
            `ref_typ`     VARCHAR(32)  NULL,
            `ref_id`      BIGINT       NULL,
            `payload`     TEXT         NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_lagefuehrung_event_org_incident_ts` (`org_id`, `incident_id`, `ts`),
            CONSTRAINT `fk_lagefuehrung_event_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_lagefuehrung_event_incident`
                FOREIGN KEY (`incident_id`) REFERENCES `incident`(`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_lagefuehrung_event_user`
                FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `lagefuehrung_event`"))
    op.execute(text("DROP TABLE IF EXISTS `lagefuehrung_feature`"))
    op.execute(text("ALTER TABLE `incident` DROP FOREIGN KEY IF EXISTS `fk_incident_lagefuehrung_fuehrer`"))
    op.execute(text("ALTER TABLE `incident` DROP COLUMN IF EXISTS `lagefuehrung_fuehrer_user_id`"))
    op.execute(text("""
        ALTER TABLE `org_settings`
        DROP COLUMN IF EXISTS `lagefuehrung_modul_aktiv`
    """))
