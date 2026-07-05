"""Objektverwaltung PR4: Objekt-Lagekarte

- objekt_karten_objekt: Marker (lat/lng) und Geometrien (GeoJSON) mit
  Symbolcode (FSD, Schluesselbox, BMZ, FBF, Gefahren-Dreiecke, Hydranten, ...)

Revision ID: 0127
Revises: 0126
Create Date: 2026-07-05
"""
from sqlalchemy import text

from alembic import op

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE IF NOT EXISTS `objekt_karten_objekt` (
            `id`            BIGINT       NOT NULL AUTO_INCREMENT,
            `org_id`        BIGINT       NULL,
            `objekt_id`     BIGINT       NOT NULL,
            `typ`           VARCHAR(40)  NOT NULL,
            `lat`           DOUBLE       NULL,
            `lng`           DOUBLE       NULL,
            `geometry_json` LONGTEXT     NULL,
            `label`         VARCHAR(100) NULL,
            `sort`          INT          NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`),
            INDEX `ix_objekt_karten_org_id` (`org_id`),
            INDEX `ix_objekt_karten_org_objekt` (`org_id`, `objekt_id`),
            CONSTRAINT `fk_objekt_karten_org` FOREIGN KEY (`org_id`)
                REFERENCES `fire_dept` (`id`) ON DELETE SET NULL,
            CONSTRAINT `fk_objekt_karten_objekt` FOREIGN KEY (`objekt_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS `objekt_karten_objekt`"))
