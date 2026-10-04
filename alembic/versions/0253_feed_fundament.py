"""Fundament fuer den gesicherten Einsatz-Pull-Feed.

Revision ID: 0253
Revises: 0252
"""

import sqlalchemy as sa

from alembic import op

revision = "0253"
down_revision = "0252"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _has_index(table: str, columns: list[str]) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index.get("column_names") == columns for index in inspector.get_indexes(table))


def _has_index_name(table: str, name: str) -> bool:
    return any(index.get("name") == name for index in sa.inspect(op.get_bind()).get_indexes(table))


def upgrade() -> None:
    if "ip_allowlist" not in _columns("api_key"):
        with op.batch_alter_table("api_key") as batch:
            batch.add_column(sa.Column("ip_allowlist", sa.Text(), nullable=True))
    if "feed_rev" not in _columns("incident"):
        with op.batch_alter_table("incident") as batch:
            batch.add_column(
                sa.Column("feed_rev", sa.BigInteger(), nullable=False, server_default="0")
            )
    if not _has_index("incident", ["primary_org_id", "status", "started_at"]):
        op.create_index(
            "ix_incident_primary_org_status_started_at",
            "incident",
            ["primary_org_id", "status", "started_at"],
        )


def downgrade() -> None:
    if _has_index_name("incident", "ix_incident_primary_org_status_started_at"):
        op.drop_index("ix_incident_primary_org_status_started_at", table_name="incident")
    if "feed_rev" in _columns("incident"):
        with op.batch_alter_table("incident") as batch:
            batch.drop_column("feed_rev")
    if "ip_allowlist" in _columns("api_key"):
        with op.batch_alter_table("api_key") as batch:
            batch.drop_column("ip_allowlist")
