"""Nachschlagewerke-Modul: OrgSettings-Feature-Flag (Muster: gateway_module_enabled)

Neues Modul "Nachschlagewerke" (Gefahrgut-Suche, Rettungsdatenblaetter,
Karten-Overlays). Aktivierung wie Objekt/UAS/Gateway ueber SystemSettings-Key
"nachschlagewerke_module_enabled" == "true" UND diese Org-Spalte.

Revision ID: 0164
Revises: 0163
Create Date: 2026-07-17
"""
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from alembic import op

revision = "0164"
down_revision = "0163"
branch_labels = None
depends_on = None


def _hat_spalte(bind, tabelle: str, spalte: str) -> bool:
    return any(c["name"] == spalte for c in sa_inspect(bind).get_columns(tabelle))


def upgrade() -> None:
    bind = op.get_bind()
    if not _hat_spalte(bind, "org_settings", "nachschlagewerke_module_enabled"):
        op.execute(text(
            "ALTER TABLE `org_settings` "
            "ADD COLUMN `nachschlagewerke_module_enabled` TINYINT(1) NOT NULL DEFAULT 0"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    if _hat_spalte(bind, "org_settings", "nachschlagewerke_module_enabled"):
        op.execute(text(
            "ALTER TABLE `org_settings` DROP COLUMN `nachschlagewerke_module_enabled`"
        ))
