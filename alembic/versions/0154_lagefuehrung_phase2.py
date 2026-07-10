"""Lageführung Phase 2: Fahrzeug-Zeichen-Mapping + Rechtevergabe

- vehicle_master.taktisches_zeichen: optionale Zuordnung eines Fahrzeugtyps zu einem
  taktischen Zeichen (id aus app/static/tz/tz-manifest.json) für die automatische
  Fahrzeug-Darstellung auf der Lagekarte.
- lagefuehrung_berechtigung: vom Lageführer explizit vergebene Editor-Rechte, ergänzt
  die rollenbasierte Editierberechtigung aus Phase 1.

Revision ID: 0154
Revises: 0153
Create Date: 2026-07-10
"""
from alembic import op
from sqlalchemy import text

revision = "0154"
down_revision = "0153"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `vehicle_master`
        ADD COLUMN IF NOT EXISTS `taktisches_zeichen` VARCHAR(64) NULL
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `lagefuehrung_berechtigung` (
            `id`                 BIGINT   NOT NULL AUTO_INCREMENT,
            `org_id`             BIGINT   NULL,
            `incident_id`        BIGINT   NOT NULL,
            `user_id`            BIGINT   NOT NULL,
            `granted_by_user_id` BIGINT   NULL,
            `granted_at`         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_lagefuehrung_berechtigung` (`incident_id`, `user_id`),
            INDEX `ix_lagefuehrung_berechtigung_org_incident` (`org_id`, `incident_id`),
            CONSTRAINT `fk_lagefuehrung_berechtigung_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_lagefuehrung_berechtigung_incident`
                FOREIGN KEY (`incident_id`) REFERENCES `incident`(`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_lagefuehrung_berechtigung_user`
                FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_lagefuehrung_berechtigung_granted_by`
                FOREIGN KEY (`granted_by_user_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `lagefuehrung_berechtigung`"))
    op.execute(text("""
        ALTER TABLE `vehicle_master`
        DROP COLUMN IF EXISTS `taktisches_zeichen`
    """))
