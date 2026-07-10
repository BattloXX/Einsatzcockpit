"""Mail-Versand je Organisation: eigener SMTP-Server + Office 365 / Microsoft Graph

- org_smtp_config: eigener SMTP-Server je Org (Fernet-verschlüsseltes Passwort),
  plus imap_* Felder als reine Vorbereitung für einen künftigen Posteingangs-Abruf
  (noch keine Verarbeitung implementiert).
- org_o365_mail_config: Microsoft Graph App-only-Mailversand je Org
  (Fernet-verschlüsseltes Client-Secret), plus read_enabled als reine
  Absichts-Flag für einen künftigen Mail.Read-Abruf (noch keine Verarbeitung
  implementiert).

Fallback-Kette beim Versand (siehe app/services/mail_service.py::deliver()):
Office 365 -> eigener SMTP der Org -> globaler SMTP (SystemSettings, unveraendert).

Revision ID: 0152
Revises: 0151
Create Date: 2026-07-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "0152"
down_revision = "0151"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = sa_inspect(bind).get_table_names()

    if "org_smtp_config" not in existing_tables:
        op.create_table(
            "org_smtp_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(),
                      sa.ForeignKey("fire_dept.id", ondelete="CASCADE"),
                      unique=True, nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("host", sa.String(255), nullable=True),
            sa.Column("port", sa.Integer(), nullable=False, server_default="587"),
            sa.Column("user", sa.String(255), nullable=True),
            sa.Column("password_enc", sa.Text(), nullable=True),
            sa.Column("from_addr", sa.String(255), nullable=True),
            sa.Column("starttls", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("timeout", sa.Integer(), nullable=False, server_default="15"),
            # Vorbereitung Posteingang (keine Verarbeitung implementiert)
            sa.Column("imap_enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("imap_host", sa.String(255), nullable=True),
            sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
            sa.Column("imap_use_ssl", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "org_o365_mail_config" not in existing_tables:
        op.create_table(
            "org_o365_mail_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(),
                      sa.ForeignKey("fire_dept.id", ondelete="CASCADE"),
                      unique=True, nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("tenant_id", sa.String(100), nullable=True),
            sa.Column("client_id", sa.String(100), nullable=True),
            sa.Column("client_secret_enc", sa.Text(), nullable=True),
            sa.Column("sender_address", sa.String(320), nullable=True),
            # Vorbereitung Posteingang (keine Verarbeitung implementiert)
            sa.Column("read_enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = sa_inspect(bind).get_table_names()
    if "org_o365_mail_config" in tables:
        op.drop_table("org_o365_mail_config")
    if "org_smtp_config" in tables:
        op.drop_table("org_smtp_config")
