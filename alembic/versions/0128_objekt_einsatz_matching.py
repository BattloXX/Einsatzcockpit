"""Objektverwaltung PR5: Einsatz-Verknuepfung + Alarm-Matching

- objekt_einsatz: Verknuepfung Einsatz ↔ Objekt (quelle bma/adresse/geo/manuell,
  status bestaetigt/vorschlag)
- org_settings.objekt_geo_match_radius_m: Radius des Geo-Fallbacks (Default 75 m)

Revision ID: 0128
Revises: 0127
Create Date: 2026-07-05
"""
from sqlalchemy import text

from alembic import op

revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_einsatz` (
            `id`                 BIGINT      NOT NULL AUTO_INCREMENT,
            `org_id`             BIGINT      NULL,
            `objekt_id`          BIGINT      NOT NULL,
            `incident_id`        BIGINT      NOT NULL,
            `quelle`             VARCHAR(20) NOT NULL,
            `status`             VARCHAR(20) NOT NULL DEFAULT 'vorschlag',
            `distanz_m`          INT         NULL,
            `erstellt_am`        DATETIME    NOT NULL,
            `bestaetigt_von_id`  BIGINT      NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_objekt_einsatz` (`incident_id`, `objekt_id`),
            INDEX `ix_objekt_einsatz_org_id` (`org_id`),
            INDEX `ix_objekt_einsatz_org_incident` (`org_id`, `incident_id`),
            INDEX `ix_objekt_einsatz_org_objekt_ts` (`org_id`, `objekt_id`, `erstellt_am`),
            CONSTRAINT `fk_objekt_einsatz_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_einsatz_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_einsatz_incident` FOREIGN KEY (`incident_id`)
                REFERENCES `incident` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_einsatz_user` FOREIGN KEY (`bestaetigt_von_id`)
                REFERENCES `user` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `objekt_geo_match_radius_m` INT NOT NULL DEFAULT 75
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `objekt_geo_match_radius_m`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_einsatz`"))
