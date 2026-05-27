"""Meldung-Status: Neue Typen (meldung/achtung/hinweis/information/erledigt/storniert)

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-27 00:00:00.000000

Migriert bestehende message.status-Werte:
  open        → meldung
  in_progress → achtung
  done        → erledigt
  cancelled   → storniert
"""
from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE message SET status = 'meldung'  WHERE status = 'open'")
    op.execute("UPDATE message SET status = 'achtung'  WHERE status = 'in_progress'")
    op.execute("UPDATE message SET status = 'erledigt' WHERE status = 'done'")
    op.execute("UPDATE message SET status = 'storniert' WHERE status = 'cancelled'")


def downgrade() -> None:
    op.execute("UPDATE message SET status = 'open'        WHERE status = 'meldung'")
    op.execute("UPDATE message SET status = 'in_progress' WHERE status = 'achtung'")
    op.execute("UPDATE message SET status = 'done'        WHERE status = 'erledigt'")
    op.execute("UPDATE message SET status = 'cancelled'   WHERE status = 'storniert'")
    # hinweis/information haben kein Legacy-Äquivalent → als open zurücksetzen
    op.execute("UPDATE message SET status = 'open' WHERE status IN ('hinweis', 'information')")
