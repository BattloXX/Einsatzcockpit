"""Incident: auto_geojson_token Spalte für den automatisch generierten GeoJSON-Feed-Token

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-28 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    conn = op.get_bind()
    return {c["name"] for c in sa_inspect(conn).get_columns(table)}


def upgrade() -> None:
    cols = _existing_columns("incident")
    if "auto_geojson_token" not in cols:
        op.add_column("incident", sa.Column("auto_geojson_token", sa.String(100), nullable=True))


def downgrade() -> None:
    cols = _existing_columns("incident")
    if "auto_geojson_token" in cols:
        op.drop_column("incident", "auto_geojson_token")
