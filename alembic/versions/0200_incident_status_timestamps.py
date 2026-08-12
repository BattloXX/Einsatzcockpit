"""Statuswechsel-Zeitpunkte am Einsatz speichern.

Revision ID: 0200
Revises: 0199
"""
import sqlalchemy as sa

from alembic import op

revision = "0200"
down_revision = "0199"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incident", sa.Column("taken_over_at", sa.DateTime(), nullable=True))
    op.add_column("incident", sa.Column("departed_at", sa.DateTime(), nullable=True))
    op.add_column("incident", sa.Column("on_scene_at", sa.DateTime(), nullable=True))
    op.add_column("incident", sa.Column("ready_again_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("incident", "ready_again_at")
    op.drop_column("incident", "on_scene_at")
    op.drop_column("incident", "departed_at")
    op.drop_column("incident", "taken_over_at")
