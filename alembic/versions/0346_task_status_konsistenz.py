"""Auftragsstatus und Abschlussmarkierungen im Altbestand abgleichen."""
from alembic import op

revision = "0346"
down_revision = "0345"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reine Konsistenzreparatur bewusst fuer den Bestand aller Organisationen.
    op.execute("UPDATE task SET status = 'cancelled', is_done = 0 "
               "WHERE status IN ('open', 'in_progress') AND is_cancelled = 1")
    op.execute("UPDATE task SET status = 'done' "
               "WHERE status IN ('open', 'in_progress') AND is_done = 1 AND is_cancelled = 0")
    op.execute("UPDATE task SET is_done = 1 WHERE status = 'done' AND is_done = 0")
    op.execute("UPDATE task SET is_cancelled = 1 WHERE status = 'cancelled' AND is_cancelled = 0")


def downgrade() -> None:
    # Reparierte Inkonsistenzen werden nicht wiederhergestellt.
    pass
