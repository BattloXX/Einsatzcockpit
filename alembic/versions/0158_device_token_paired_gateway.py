"""Geraete-Token <-> SMS-Gateway-Token verknuepfen (kombiniertes Geraet)

Bislang gab es bei "Geraet + SMS-Gateway" keine DB-Verknuepfung zwischen dem
DeviceToken und dem gleichzeitig erzeugten SmsGatewayToken - der QR-Code
kodierte beide Tokens direkt, aber die PIN-Pairing-Alternative (Admin-Login
ohne Kamerazugriff) kannte nur den Geraete-Token und konnte die Gateway-Rolle
daher nie aktivieren (Nutzer-Feedback 2026-07-11: gepaartes Testgeraet zeigte
keine SMS-Gateway-Karte in der About-App-Seite).

Revision ID: 0158
Revises: 0157
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0158"
down_revision = "0157"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_token",
        sa.Column("paired_gateway_token_id", sa.BigInteger,
                   sa.ForeignKey("sms_gateway_token.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_token", "paired_gateway_token_id")
