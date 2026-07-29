"""BMA-Import: kontakte_uebernommen fuer Alt-Datensaetze nachziehen

Migration 0184 hat kontakte_uebernommen fuer ALLE bestehenden bma_import_satz-
Zeilen auf False (Default) gesetzt - auch fuer Saetze, bei denen tatsaechlich
schon einmal Kontakte importiert wurden, bevor es dieses Flag gab (Vorfall
Objekt 916/Hotel Sternen Wolfurt: Kontakte wurden am 27.07. importiert, am
29.07. manuell geloescht, ein erneuter PDF-Upload meldete trotz Fix in 0184
weiterhin "keine Aenderung", weil _passender_bestaetigter_stand() den
Live-Abgleich bei kontakte_uebernommen=False uebersprungen hat).

Backfill-Heuristik: objekt_change (Aenderungsprotokoll) enthaelt bereits einen
zuverlaessigen Beweis dafuer, dass fuer ein Objekt jemals echte BMA-Kontakte
geschrieben wurden - _sync_kontakte() in bma_sync.py schreibt bei jeder
tatsaechlichen Kontakt-Aenderung einen Eintrag bereich='kontakte',
feld='bma_import_sync'. uebernimm_arbeitskopie() haengt diese Eintraege beim
Merge auf die produktive Objekt-id um (objekt_service.py::uebernimm_arbeitskopie),
die Eintraege sind also unabhaengig davon, ob die Kontakte per Auto-Apply
(Entwurf) oder per "Uebernehmen" (Arbeitskopie) geschrieben wurden unter der
stabilen Objekt-id auffindbar.

Revision ID: 0185
Revises: 0184
Create Date: 2026-07-29
"""
from sqlalchemy import text

from alembic import op

revision = "0185"
down_revision = "0184"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    conn.execute(text(
        "UPDATE bma_import_satz s"
        " SET s.kontakte_uebernommen = TRUE"
        " WHERE s.kontakte_uebernommen = FALSE"
        " AND s.objekt_id IS NOT NULL"
        " AND EXISTS ("
        "   SELECT 1 FROM objekt_change oc"
        "   WHERE oc.objekt_id = s.objekt_id"
        "     AND oc.bereich = 'kontakte'"
        "     AND oc.feld = 'bma_import_sync'"
        " )"
    ))


def downgrade() -> None:
    # Datenkorrektur, kein Rollback vorgesehen (das Flag auf False zu
    # zuruecksetzen wuerde denselben Bug wieder herstellen).
    pass
