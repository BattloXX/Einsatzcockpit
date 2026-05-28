"""Lagekarte.info Integration: Koordinaten an Incident + FireDept, neue Tabelle lagekarte_token

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-28 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Direkte ADD COLUMN Operationen — funktioniert in MySQL/MariaDB und SQLite
    # (SQLite unterstützt ALTER TABLE ADD COLUMN für nullable Spalten ohne Default)
    op.add_column("incident", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("incident", sa.Column("lng", sa.Float(), nullable=True))
    op.add_column("incident", sa.Column("lagekarte_shash_url", sa.String(500), nullable=True))

    op.add_column("fire_dept", sa.Column("fallback_lat", sa.Float(), nullable=True))
    op.add_column("fire_dept", sa.Column("fallback_lng", sa.Float(), nullable=True))

    # lagekarte_token: explizit InnoDB damit FKs in MariaDB funktionieren
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
    op.drop_table("lagekarte_token")

    op.drop_column("fire_dept", "fallback_lng")
    op.drop_column("fire_dept", "fallback_lat")

    op.drop_column("incident", "lagekarte_shash_url")
    op.drop_column("incident", "lng")
    op.drop_column("incident", "lat")
