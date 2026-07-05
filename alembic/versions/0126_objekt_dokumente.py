"""Objektverwaltung PR3: Dokumenten-Pipeline

- objekt_dokument: Original-PDF (Status-Workflow neu/verarbeitung/fertig/fehler,
  belegt_bytes fuer Quota-Freigabe)
- objekt_dokument_seite: zerlegte Einzelseite (pypdf-PDF + pdf2image-Rendering)
  mit Klassifizierung (Dokumentart, Titel, Melderlinien, Stand, bei_einsatz_drucken)

Deployment-Hinweis: Rasterung braucht Poppler (`apt install poppler-utils`).

Revision ID: 0126
Revises: 0125
Create Date: 2026-07-05
"""
from sqlalchemy import text

from alembic import op

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_dokument` (
            `id`                 BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`             BIGINT       NULL,
            `objekt_id`          BIGINT       NOT NULL,
            `dateiname_original` VARCHAR(255) NOT NULL,
            `pfad`               VARCHAR(500) NOT NULL,
            `mime`               VARCHAR(100) NOT NULL DEFAULT 'application/pdf',
            `groesse_bytes`      BIGINT       NOT NULL DEFAULT 0,
            `belegt_bytes`       BIGINT       NOT NULL DEFAULT 0,
            `seitenzahl`         INT          NOT NULL DEFAULT 0,
            `status`             VARCHAR(20)  NOT NULL DEFAULT 'neu',
            `fehler_text`        VARCHAR(500) NULL,
            `hochgeladen_von_id` BIGINT       NULL,
            `hochgeladen_am`     DATETIME     NOT NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_objekt_dokument_org_id` (`org_id`),
            INDEX `ix_objekt_dokument_org_objekt` (`org_id`, `objekt_id`),
            CONSTRAINT `fk_objekt_dokument_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_dokument_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_dokument_user` FOREIGN KEY (`hochgeladen_von_id`)
                REFERENCES `user` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))

    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_dokument_seite` (
            `id`                   BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`               BIGINT       NULL,
            `objekt_id`            BIGINT       NOT NULL,
            `dokument_id`          BIGINT       NOT NULL,
            `seiten_nr`            INT          NOT NULL,
            `einzel_pdf_pfad`      VARCHAR(500) NULL,
            `bild_pfad`            VARCHAR(500) NULL,
            `thumb_pfad`           VARCHAR(500) NULL,
            `dokumentart`          VARCHAR(30)  NULL,
            `titel`                VARCHAR(200) NULL,
            `melderlinien`         VARCHAR(100) NULL,
            `stand`                DATE         NULL,
            `bei_einsatz_drucken`  TINYINT(1)   NOT NULL DEFAULT 0,
            `klassifiziert_von_id` BIGINT       NULL,
            `klassifiziert_am`     DATETIME     NULL,
            PRIMARY KEY (`id`),
            INDEX `ix_objekt_seite_org_id` (`org_id`),
            INDEX `ix_objekt_seite_org_objekt_art` (`org_id`, `objekt_id`, `dokumentart`),
            INDEX `ix_objekt_seite_dokument` (`org_id`, `objekt_id`, `dokument_id`, `seiten_nr`),
            CONSTRAINT `fk_objekt_seite_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_seite_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_seite_dokument` FOREIGN KEY (`dokument_id`)
                REFERENCES `objekt_dokument` (`id`) ON DELETE CASCADE,
            CONSTRAINT `fk_objekt_seite_user` FOREIGN KEY (`klassifiziert_von_id`)
                REFERENCES `user` (`id`) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `objekt_dokument_seite`"))
    op.execute(text("DROP TABLE IF EXISTS `objekt_dokument`"))
