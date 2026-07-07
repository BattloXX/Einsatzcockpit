"""Bild-Annotation: gemeinsame polymorphe Annotations-Tabelle + Versionen

- media_annotation: eine Zeile je annotierbarem Bild (media_typ + media_id),
  haelt Konva-Vektordaten, flaches PNG, Soft-Lock und Herkunft (Objektuebernahme).
- media_annotation_version: leichtgewichtiges Archiv je Speichervorgang.

Revision ID: 0139
Revises: 0138
Create Date: 2026-07-07
"""
from sqlalchemy import text

from alembic import op

revision = "0139"
down_revision = "0138"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `media_annotation` (
            `id`                 BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`             BIGINT       NULL,
            `media_typ`          VARCHAR(16)  NOT NULL,
            `media_id`           BIGINT       NOT NULL,
            `annotation_json`    LONGTEXT     NULL,
            `annotated_file`     VARCHAR(500) NULL,
            `annotated_at`       DATETIME     NULL,
            `annotated_by`       BIGINT       NULL,
            `locked_by`          BIGINT       NULL,
            `locked_at`          DATETIME     NULL,
            `source_objekt_id`   BIGINT       NULL,
            `source_dokument_id` BIGINT       NULL,
            `source_seite`       INT          NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uq_media_annotation_target` (`media_typ`, `media_id`),
            KEY `ix_media_annotation_org` (`org_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `media_annotation_version` (
            `id`              BIGINT   NOT NULL AUTO_INCREMENT,
            `annotation_id`   BIGINT   NOT NULL,
            `annotation_json` LONGTEXT NOT NULL,
            `created_at`      DATETIME NOT NULL,
            `created_by`      BIGINT   NULL,
            PRIMARY KEY (`id`),
            KEY `ix_media_annotation_version_ann` (`annotation_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `media_annotation_version`"))
    op.execute(text("DROP TABLE IF EXISTS `media_annotation`"))
