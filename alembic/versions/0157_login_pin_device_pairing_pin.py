"""SMS-PIN-Login (login_pin) + Geraete-Pairing-PIN (device_token)

Zwei neue, alternative Anmeldewege zum bisherigen QR-Code-Scan/Passwort-Login:
- login_pin: Einmal-PIN per SMS an die hinterlegte Telefonnummer eines Users.
- device_token.pairing_pin_hash/-expires_at: kurzlebige, abtippbare PIN als
  Alternative zum QR-Code-Scan beim Geraete-Login (z.B. ohne Kamerazugriff).

Revision ID: 0157
Revises: 0156
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0157"
down_revision = "0156"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_pin",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("pin_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("requesting_ip", sa.String(64), nullable=True),
    )
    op.add_column("device_token", sa.Column("pairing_pin_hash", sa.String(64), nullable=True))
    op.add_column("device_token", sa.Column("pairing_pin_expires_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("device_token", "pairing_pin_expires_at")
    op.drop_column("device_token", "pairing_pin_hash")
    op.drop_table("login_pin")
