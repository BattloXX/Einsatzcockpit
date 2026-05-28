"""Lagekarte.info Integration: Koordinaten an Incident + FireDept, neue Tabelle lagekarte_token

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-28 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    conn = op.get_bind()
    return {c["name"] for c in sa_inspect(conn).get_columns(table)}


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    return table in sa_inspect(conn).get_table_names()


def upgrade() -> None:
    # Idempotent: Spalten nur hinzufügen wenn sie noch nicht existieren
    # (ein fehlgeschlagener Vorlauf kann Spalten bereits angelegt haben)

    incident_cols = _existing_columns("incident")
    if "lat" not in incident_cols:
        op.add_column("incident", sa.Column("lat", sa.Float(), nullable=True))
    if "lng" not in incident_cols:
        op.add_column("incident", sa.Column("lng", sa.Float(), nullable=True))
    if "lagekarte_shash_url" not in incident_cols:
        op.add_column("incident", sa.Column("lagekarte_shash_url", sa.String(500), nullable=True))

    fire_dept_cols = _existing_columns("fire_dept")
    if "fallback_lat" not in fire_dept_cols:
        op.add_column("fire_dept", sa.Column("fallback_lat", sa.Float(), nullable=True))
    if "fallback_lng" not in fire_dept_cols:
        op.add_column("fire_dept", sa.Column("fallback_lng", sa.Float(), nullable=True))

    if not _table_exists("lagekarte_token"):
        op.create_table(
            "lagekarte_token",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("label", sa.String(150), nullable=False),
            sa.Column("org_id", sa.Integer(), nullable=False),
            sa.Column("einsatz_id", sa.BigInteger(), nullable=True),
            sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["einsatz_id"], ["incident.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )


def downgrade() -> None:
    if _table_exists("lagekarte_token"):
        op.drop_table("lagekarte_token")

    fire_dept_cols = _existing_columns("fire_dept")
    if "fallback_lng" in fire_dept_cols:
        op.drop_column("fire_dept", "fallback_lng")
    if "fallback_lat" in fire_dept_cols:
        op.drop_column("fire_dept", "fallback_lat")

    incident_cols = _existing_columns("incident")
    if "lagekarte_shash_url" in incident_cols:
        op.drop_column("incident", "lagekarte_shash_url")
    if "lng" in incident_cols:
        op.drop_column("incident", "lng")
    if "lat" in incident_cols:
        op.drop_column("incident", "lat")
