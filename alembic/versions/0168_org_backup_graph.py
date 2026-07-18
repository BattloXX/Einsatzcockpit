"""Org-Backup: Microsoft-Graph-Ziel (SharePoint/OneDrive) in org_backup_config.

Fuegt graph_tenant_id/client_id/client_secret_enc/drive_id/folder hinzu.

Revision ID: 0168
Revises: 0167
Create Date: 2026-07-18
"""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "0168"
down_revision = "0167"
branch_labels = None
depends_on = None

_SPALTEN = [
    ("graph_tenant_id", sa.String(100)),
    ("graph_client_id", sa.String(100)),
    ("graph_client_secret_enc", sa.Text()),
    ("graph_drive_id", sa.String(255)),
    ("graph_folder", sa.String(500)),
]


def upgrade() -> None:
    bind = op.get_bind()
    vorhanden = {c["name"] for c in sa_inspect(bind).get_columns("org_backup_config")}
    for name, typ in _SPALTEN:
        if name not in vorhanden:
            op.add_column("org_backup_config", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    vorhanden = {c["name"] for c in sa_inspect(bind).get_columns("org_backup_config")}
    for name, _ in _SPALTEN:
        if name in vorhanden:
            op.drop_column("org_backup_config", name)
