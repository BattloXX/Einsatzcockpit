"""org_dibos_config: DIBOS-EventHub/Elvis-Anbindung je Org (Muster: org_lis_config)

Neue 1:1-Config-Tabelle fuer die DIBOS-EventHub-Anbindung (Landeswarnzentrale
Vorarlberg, Elvis-Desktop-Client) — reines Tracing/Diagnose-Feature, siehe
app/services/dibos/. Zwei getrennte Fernet-verschluesselte Zugangsdaten
(Gateway-Konto per HTTP-Basic, Org-Servicekonto per WS-Security-UsernameToken),
da die Schnittstelle beide unabhaengig voneinander benoetigt.

Revision ID: 0163
Revises: 0162
Create Date: 2026-07-15
"""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "0163"
down_revision = "0162"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    if "org_dibos_config" not in sa_inspect(bind).get_table_names():
        op.create_table(
            "org_dibos_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(),
                      sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("base_url", sa.String(300), nullable=True),
            sa.Column("host", sa.String(100), nullable=False, server_default="einsatzcockpit"),
            sa.Column("ag", sa.String(10), nullable=False, server_default="FW"),
            sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="20"),
            sa.Column("auto_trace_on_event", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("auto_trace_duration_minutes", sa.Integer(), nullable=False, server_default="120"),
            sa.Column("gateway_user", sa.String(100), nullable=True),
            sa.Column("gateway_password_enc", sa.Text(), nullable=True),
            sa.Column("service_user", sa.String(150), nullable=True),
            sa.Column("service_password_enc", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            mysql_charset="utf8mb4",
            mysql_engine="InnoDB",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "org_dibos_config" in sa_inspect(bind).get_table_names():
        op.drop_table("org_dibos_config")
