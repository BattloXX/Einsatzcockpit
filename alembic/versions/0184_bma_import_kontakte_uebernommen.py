"""BMA-Import: Flag fuer tatsaechlich uebernommene Kontakte

- bma_import_satz.kontakte_uebernommen: True, sobald fuer diesen Satz mindestens
  einmal wende_anlage_auf_objekt_an() mit echten Kontakten gelaufen ist
  (Uebernehmen/Auto-Apply, NICHT Ignorieren). Ohne dieses Flag kann der
  Live-Abgleich nicht zwischen "Vorschlag nie uebernommen" (0 Kontakte ist hier
  normal) und "Kontakte wurden uebernommen, dann manuell geloescht" (Vorfall
  Objekt 916/Hotel Sternen Wolfurt) unterscheiden - siehe
  app/services/bma_import/bma_sync.py::_passender_bestaetigter_stand().

Revision ID: 0184
Revises: 0183
Create Date: 2026-07-29
"""
from sqlalchemy import text

from alembic import op

revision = "0184"
down_revision = "0183"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    r = conn.execute(text(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"t": table, "c": column})
    return (r.scalar() or 0) > 0


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql" or not _column_exists(conn, "bma_import_satz", "kontakte_uebernommen"):
        op.execute(text(
            "ALTER TABLE `bma_import_satz` ADD COLUMN `kontakte_uebernommen` "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        ))


def downgrade() -> None:
    op.execute(text("ALTER TABLE `bma_import_satz` DROP COLUMN `kontakte_uebernommen`"))
