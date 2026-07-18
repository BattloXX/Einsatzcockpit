"""Org-Backup: partielles Backup (include_areas) in org_backup_config.

Komma-Liste gewaehlter Bereiche (org_export_service.AREA_ROOTS). NULL = vollstaendig.

Revision ID: 0167
Revises: 0166
Create Date: 2026-07-18
"""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "0167"
down_revision = "0166"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa_inspect(bind).get_columns("org_backup_config")}
    if "include_areas" not in cols:
        op.add_column("org_backup_config", sa.Column("include_areas", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa_inspect(bind).get_columns("org_backup_config")}
    if "include_areas" in cols:
        op.drop_column("org_backup_config", "include_areas")
