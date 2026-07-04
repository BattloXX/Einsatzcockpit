"""Spalte "Disponierte Fahrzeuge" (dispatched) entfernen

Fahrzeuge werden jetzt entweder direkt (ohne LIS) oder erst mit Status S4 /
manuell (mit LIS) der Spalte "Tatsaechlich im Einsatz" (active) zugeordnet -
die Zwischenspalte "dispatched" entfaellt. Bestehende Fahrzeuge in "dispatched"
werden nach "active" verschoben (ans Ende, ueber einen Offset auf
display_order), danach werden alle "dispatched"-Spalten geloescht.

Revision ID: 0120
Revises: 0119
Create Date: 2026-07-05
"""
from alembic import op
from sqlalchemy import text

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fahrzeuge aus "dispatched" nach "active" verschieben (gleicher Einsatz),
    # ans Ende der Ziel-Spalte anhaengen statt die bestehende Reihenfolge zu stoeren.
    op.execute(text("""
        UPDATE incident_vehicle iv
        JOIN incident_column dispatched
            ON dispatched.id = iv.column_id AND dispatched.code = 'dispatched'
        JOIN incident_column active
            ON active.incident_id = dispatched.incident_id AND active.code = 'active'
        SET iv.column_id = active.id,
            iv.display_order = iv.display_order + 100000
    """))
    op.execute(text("DELETE FROM incident_column WHERE code = 'dispatched'"))


def downgrade() -> None:
    # Nicht sinnvoll rueckgaengig zu machen (welche Fahrzeuge urspruenglich in
    # "dispatched" waren, ist nach dem Upgrade nicht mehr rekonstruierbar).
    pass
