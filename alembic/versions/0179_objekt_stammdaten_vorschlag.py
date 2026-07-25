"""Objektverwaltung: KI-Stammdaten-Extraktion aus Objektbeschreibungs-Seiten

- objekt_stammdaten_vorschlag: strukturierter Extraktionsvorschlag
  (Objekt.informationen/ObjektBMA/ObjektGefahr/ObjektMerkmal), Review-Queue
  analog objekt_seite_ki_vorschlag, nie Auto-Apply.

Revision ID: 0179
Revises: 0178
Create Date: 2026-07-24
"""
from sqlalchemy import text

from alembic import op

revision = "0179"
down_revision = "0178"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_stammdaten_vorschlag` (
            `id`                      BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`                  BIGINT       NULL,
            `objekt_id`               BIGINT       NOT NULL,
            `seite_id`                BIGINT       NOT NULL,
            `informationen_text`      TEXT         NULL,
            `bma_nummer`              VARCHAR(50)  NULL,
            `bmz_standort`            VARCHAR(300) NULL,
            `fbf_standort`            VARCHAR(300) NULL,
            `laufkarten_ablageort`    VARCHAR(300) NULL,
            `schluesselsafe_standort` VARCHAR(300) NULL,
            `gefahren_json`           TEXT         NULL,
            `merkmale_json`           TEXT         NULL,
            `begruendung`             VARCHAR(300) NULL,
            `status`                  VARCHAR(20)  NOT NULL DEFAULT 'offen',
            `erstellt_am`             DATETIME     NOT NULL,
            `entschieden_von_id`      BIGINT       NULL,
            `entschieden_am`          DATETIME     NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_objekt_stammdaten_vorschlag_org_id` (`org_id`),
            INDEX `ix_objekt_stammdaten_vorschlag_org_status` (`org_id`, `status`),
            CONSTRAINT `fk_objekt_stammdaten_vorschlag_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_stammdaten_vorschlag_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_stammdaten_vorschlag_seite` FOREIGN KEY (`seite_id`)
                REFERENCES `objekt_dokument_seite` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_stammdaten_vorschlag_user` FOREIGN KEY (`entschieden_von_id`)
                REFERENCES `user` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `objekt_stammdaten_vorschlag`"))
