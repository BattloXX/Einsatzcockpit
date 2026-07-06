"""Gefahren: weiterfuehrende Links + Gefahrgut-DB-Anreicherung

- gefahren_katalog.links_json (Standard-Links je Gefahrenart)
- objekt_gefahr.links_json (objektspezifische Links) + stoffname/gefahrklasse/gefahrnummer

Revision ID: 0135
Revises: 0134
Create Date: 2026-07-06
"""
from sqlalchemy import text

from alembic import op

revision = "0135"
down_revision = "0134"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE `gefahren_katalog` ADD COLUMN `links_json` LONGTEXT NULL"))
    op.execute(text(
        "ALTER TABLE `objekt_gefahr` "
        "ADD COLUMN `stoffname` VARCHAR(200) NULL, "
        "ADD COLUMN `gefahrklasse` VARCHAR(40) NULL, "
        "ADD COLUMN `gefahrnummer` VARCHAR(20) NULL, "
        "ADD COLUMN `links_json` LONGTEXT NULL"
    ))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `gefahren_katalog` DROP COLUMN `links_json`"))
    op.execute(text(
        "ALTER TABLE `objekt_gefahr` "
        "DROP COLUMN `stoffname`, DROP COLUMN `gefahrklasse`, "
        "DROP COLUMN `gefahrnummer`, DROP COLUMN `links_json`"
    ))
