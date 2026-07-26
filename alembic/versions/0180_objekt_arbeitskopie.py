"""Objektverwaltung: Arbeitskopie-Workflow (Entwurf -> Freigabe -> Ueberarbeitung)

- objekt.nummer wird nullable: Arbeitskopien (entwurf_von_id gesetzt) bekommen KEINE
  eigene Nummer, sonst wuerde uq_objekt_org_nummer mit dem produktiven Objekt
  kollidieren. MySQL behandelt mehrere NULLs in einem UNIQUE-Index als verschieden.
- objekt.entwurf_von_id: Self-FK, gesetzt <=> diese Zeile ist eine Arbeitskopie des
  referenzierten (produktiven) Objekts. UNIQUE erzwingt maximal eine offene
  Arbeitskopie je produktivem Objekt. ON DELETE CASCADE: eine offene Arbeitskopie
  faellt mit weg, wenn das produktive Objekt geloescht wird.

Siehe docs/plans/objekt-arbeitskopie-plan.md fuer das Gesamtkonzept.

Revision ID: 0180
Revises: 0179
Create Date: 2026-07-25
"""
from sqlalchemy import text

from alembic import op

revision = "0180"
down_revision = "0179"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `objekt`
            MODIFY COLUMN `nummer` INT NULL
    """))
    op.execute(text("""
        ALTER TABLE `objekt`
            ADD COLUMN `entwurf_von_id` BIGINT NULL AFTER `status`
    """))
    op.execute(text("""
        ALTER TABLE `objekt`
            ADD CONSTRAINT `uq_objekt_entwurf_von` UNIQUE (`entwurf_von_id`)
    """))
    op.execute(text("""
        ALTER TABLE `objekt`
            ADD CONSTRAINT `fk_objekt_entwurf_von` FOREIGN KEY (`entwurf_von_id`)
                REFERENCES `objekt` (`id`) ON DELETE CASCADE
    """))


def downgrade() -> None:
    # Offene Arbeitskopien koennen nicht verlustfrei zurueckgebaut werden (die Basis
    # kennt ihre Aenderungen nicht) - sie werden vor dem Downgrade geloescht.
    op.execute(text("DELETE FROM `objekt` WHERE `entwurf_von_id` IS NOT NULL"))
    op.execute(text("ALTER TABLE `objekt` DROP FOREIGN KEY `fk_objekt_entwurf_von`"))
    op.execute(text("ALTER TABLE `objekt` DROP INDEX `uq_objekt_entwurf_von`"))
    op.execute(text("ALTER TABLE `objekt` DROP COLUMN `entwurf_von_id`"))
    op.execute(text("ALTER TABLE `objekt` MODIFY COLUMN `nummer` INT NOT NULL"))
