"""Objektverwaltung PR8: KI-Klassifizierung (Review-Queue)

- objekt_seite_ki_vorschlag: Vision-Vorschlaege je Dokumentseite
  (offen/uebernommen/verworfen, nie Auto-Apply)
- org_settings.objekt_ki_klassifikation_enabled: Opt-in je Org

Revision ID: 0130
Revises: 0129
Create Date: 2026-07-05
"""
from sqlalchemy import text

from alembic import op

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_seite_ki_vorschlag` (
            `id`                  BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`              BIGINT       NULL,
            `seite_id`            BIGINT       NOT NULL,
            `dokumentart`         VARCHAR(30)  NULL,
            `titel`               VARCHAR(200) NULL,
            `melderlinien`        VARCHAR(100) NULL,
            `stand`               DATE         NULL,
            `begruendung`         VARCHAR(300) NULL,
            `status`              VARCHAR(20)  NOT NULL DEFAULT 'offen',
            `erstellt_am`         DATETIME     NOT NULL,
            `entschieden_von_id`  BIGINT       NULL,
            `entschieden_am`      DATETIME     NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_objekt_ki_vorschlag_org_id` (`org_id`),
            INDEX `ix_objekt_ki_vorschlag_org_status` (`org_id`, `status`),
            CONSTRAINT `fk_objekt_ki_vorschlag_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_ki_vorschlag_seite` FOREIGN KEY (`seite_id`)
                REFERENCES `objekt_dokument_seite` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_ki_vorschlag_user` FOREIGN KEY (`entschieden_von_id`)
                REFERENCES `user` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))
    op.execute(text("""
        ALTER TABLE `org_settings`
        ADD COLUMN IF NOT EXISTS `objekt_ki_klassifikation_enabled` TINYINT(1) NOT NULL DEFAULT 0
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_settings` DROP COLUMN IF EXISTS `objekt_ki_klassifikation_enabled`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_seite_ki_vorschlag`"))
