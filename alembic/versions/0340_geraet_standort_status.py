"""Standortmeldung der Geraete: Messzeit, Empfang, Status und Fehler."""
import sqlalchemy as sa

from alembic import op

revision = "0340"
down_revision = "0339"
branch_labels = None
depends_on = None

SPALTEN = (
    ("last_location_received_at", sa.DateTime()),
    ("last_location_attempt_at", sa.DateTime()),
    ("last_location_accuracy_m", sa.Float()),
    ("last_location_status", sa.String(30)),
    ("last_location_source", sa.String(20)),
    ("last_location_error", sa.String(200)),
)


def upgrade() -> None:
    for name, typ in SPALTEN:
        op.add_column("device_token", sa.Column(name, typ, nullable=True))
    # Bisherige Positionen galten mit Empfangszeit; das ist der beste bekannte Wert.
    op.execute("UPDATE device_token SET last_location_received_at = last_location_at, "
               "last_location_status = 'ok' WHERE last_location_at IS NOT NULL")


def downgrade() -> None:
    for name, _ in reversed(SPALTEN):
        op.drop_column("device_token", name)
