"""Lagekarte.info Integration: Koordinaten an Incident + FireDept, neue Tabelle lagekarte_token

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-28 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- incident: lat, lng, lagekarte_shash_url ---
    with op.batch_alter_table("incident", recreate="auto") as batch:
        batch.add_column(sa.Column("lat", sa.Float(), nullable=True))
        batch.add_column(sa.Column("lng", sa.Float(), nullable=True))
        batch.add_column(sa.Column("lagekarte_shash_url", sa.String(500), nullable=True))

    # --- fire_dept: fallback_lat, fallback_lng ---
    with op.batch_alter_table("fire_dept", recreate="auto") as batch:
        batch.add_column(sa.Column("fallback_lat", sa.Float(), nullable=True))
        batch.add_column(sa.Column("fallback_lng", sa.Float(), nullable=True))

    # --- lagekarte_token: neue Tabelle ---
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
    )


def downgrade() -> None:
    op.drop_table("lagekarte_token")

    with op.batch_alter_table("fire_dept", recreate="auto") as batch:
        batch.drop_column("fallback_lng")
        batch.drop_column("fallback_lat")

    with op.batch_alter_table("incident", recreate="auto") as batch:
        batch.drop_column("lagekarte_shash_url")
        batch.drop_column("lng")
        batch.drop_column("lat")
