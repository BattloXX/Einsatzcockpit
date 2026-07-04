"""LIS/IPR: Passwort optional bereits als SHA1-Hash hinterlegbar

- org_lis_config.password_is_hash: wenn gesetzt, wird der in password_enc
  gespeicherte Wert unveraendert als Login-Passwort-Hash verwendet statt ihn
  selbst per SHA1 zu hashen (siehe lis_client.py::login()). Erlaubt Betreibern,
  die nur den Hash (nicht das Klartext-Passwort) herausgeben, die Anbindung
  trotzdem einzurichten.

Revision ID: 0121
Revises: 0120
Create Date: 2026-07-05
"""
from alembic import op
from sqlalchemy import text

revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE `org_lis_config`
        ADD COLUMN IF NOT EXISTS `password_is_hash` TINYINT(1) NOT NULL DEFAULT 0
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `org_lis_config` DROP COLUMN IF EXISTS `password_is_hash`"))
