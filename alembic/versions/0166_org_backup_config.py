"""Self-Service-Backup je Organisation: Remote-Ziel + Zeitplan (org_backup_config).

Je Org 1:1-Config: eigenes Backup-Ziel (Protokoll/Host/Zugangsdaten, Secrets
Fernet-verschluesselt), Zeitplan und Retention. Basis fuer den geplanten Push der
tenant-gescopten Org-Daten (app/services/org_export_service.py) an ein selbst
konfiguriertes Ziel.

Revision ID: 0166
Revises: 0165
Create Date: 2026-07-18
"""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "0166"
down_revision = "0165"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = sa_inspect(bind).get_table_names()

    if "org_backup_config" not in existing_tables:
        op.create_table(
            "org_backup_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.BigInteger(),
                      sa.ForeignKey("fire_dept.id", ondelete="CASCADE"),
                      unique=True, nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("protocol", sa.String(10), nullable=False, server_default="sftp"),
            sa.Column("host", sa.String(255), nullable=True),
            sa.Column("port", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("username", sa.String(255), nullable=True),
            sa.Column("password_enc", sa.Text(), nullable=True),
            sa.Column("ssh_key_enc", sa.Text(), nullable=True),
            sa.Column("remote_path", sa.String(500), nullable=True),
            sa.Column("ssh_strict", sa.String(20), nullable=False, server_default="accept-new"),
            sa.Column("rclone_remote", sa.String(255), nullable=True),
            sa.Column("schedule", sa.String(10), nullable=False, server_default="daily"),
            sa.Column("hour", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("weekday", sa.Integer(), nullable=True),
            sa.Column("keep_count", sa.Integer(), nullable=False, server_default="7"),
            sa.Column("include_media", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_status", sa.String(20), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "org_backup_config" in sa_inspect(bind).get_table_names():
        op.drop_table("org_backup_config")
