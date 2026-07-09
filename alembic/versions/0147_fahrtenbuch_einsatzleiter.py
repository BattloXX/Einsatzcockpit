"""Fahrtenbuch: optionale Einsatzleiter-Abfrage

Revision ID: 0147
Revises: 0146
Create Date: 2026-07-09
"""
from alembic import op
from sqlalchemy import text

revision = "0147"
down_revision = "0146"
branch_labels = None
depends_on = None


def upgrade():
    # Fahrzeug-Flag: steuert, ob im Erfassungsformular ein Einsatzleiter-Feld erscheint.
    op.execute(text("""
        ALTER TABLE `vehicle_master`
        ADD COLUMN IF NOT EXISTS `einsatzleiter_abfrage` TINYINT(1) NOT NULL DEFAULT 0
    """))
    # Fahrt: Einsatzleiter (optional) – Member-Referenz + denormalisierter Name.
    op.execute(text("""
        ALTER TABLE `fahrt`
        ADD COLUMN IF NOT EXISTS `einsatzleiter_member_id` BIGINT NULL,
        ADD COLUMN IF NOT EXISTS `einsatzleiter_name` VARCHAR(160) NULL
    """))
    op.execute(text("""
        ALTER TABLE `fahrt`
        ADD CONSTRAINT `fk_fahrt_einsatzleiter`
            FOREIGN KEY (`einsatzleiter_member_id`) REFERENCES `member`(`id`) ON DELETE SET NULL
    """))


def downgrade():
    op.execute(text("ALTER TABLE `fahrt` DROP FOREIGN KEY `fk_fahrt_einsatzleiter`"))
    op.execute(text("""
        ALTER TABLE `fahrt`
        DROP COLUMN IF EXISTS `einsatzleiter_member_id`,
        DROP COLUMN IF EXISTS `einsatzleiter_name`
    """))
    op.execute(text("""
        ALTER TABLE `vehicle_master`
        DROP COLUMN IF EXISTS `einsatzleiter_abfrage`
    """))
