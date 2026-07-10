"""Lageführung Phase 3: Momentaufnahme-Tabelle

- lagefuehrung_snapshot: PNG-Export des Kartenstands ("Lage einfrieren"), verlinkt an ein
  lagefuehrung_event (event_typ='snapshot.erstellt', ref_typ='snapshot', ref_id=snapshot.id).
- Lage-Replay und Windrichtung (Phase 3) brauchen kein Schema — Replay nutzt bereits
  vorhandene lagefuehrung_event-Payloads (angereichert in ui_lagefuehrung.py), Windrichtung
  nutzt den bestehenden typ='taktisches_zeichen' mit einem neuen Manifest-Eintrag.

Revision ID: 0155
Revises: 0154
Create Date: 2026-07-10
"""
from alembic import op
from sqlalchemy import text

revision = "0155"
down_revision = "0154"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `lagefuehrung_snapshot` (
            `id`               BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`           BIGINT       NULL,
            `incident_id`      BIGINT       NOT NULL,
            `stored_filename`  VARCHAR(80)  NOT NULL,
            `bytes`            INT          NOT NULL DEFAULT 0,
            `label`            VARCHAR(255) NULL,
            `created_by`       BIGINT       NULL,
            `created_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            INDEX `ix_lagefuehrung_snapshot_org_incident` (`org_id`, `incident_id`),
            CONSTRAINT `fk_lagefuehrung_snapshot_org`
                FOREIGN KEY (`org_id`) REFERENCES `fire_dept`(`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_lagefuehrung_snapshot_incident`
                FOREIGN KEY (`incident_id`) REFERENCES `incident`(`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_lagefuehrung_snapshot_created_by`
                FOREIGN KEY (`created_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `lagefuehrung_snapshot`"))
