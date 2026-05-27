"""Atemschutzüberwachung: Leitfaden/FwDV-7-konforme Erweiterung

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-27 00:00:00.000000

Neue Spalten:
  breathing_troop: planned_duration_min, bottle_preset, location_text,
                   last_meldung_at, last_meldung_text,
                   warn_one_third_acked_at, warn_max_time_acked_at,
                   warn_withdraw_acked_at
  pressure_log:    note
"""
from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("breathing_troop") as batch:
        batch.add_column(sa.Column("planned_duration_min", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("bottle_preset", sa.String(20), nullable=True))
        batch.add_column(sa.Column("location_text", sa.String(200), nullable=True))
        batch.add_column(sa.Column("last_meldung_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_meldung_text", sa.String(500), nullable=True))
        batch.add_column(sa.Column("warn_one_third_acked_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("warn_max_time_acked_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("warn_withdraw_acked_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("pressure_log") as batch:
        batch.add_column(sa.Column("note", sa.String(300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pressure_log") as batch:
        batch.drop_column("note")

    with op.batch_alter_table("breathing_troop") as batch:
        batch.drop_column("warn_withdraw_acked_at")
        batch.drop_column("warn_max_time_acked_at")
        batch.drop_column("warn_one_third_acked_at")
        batch.drop_column("last_meldung_text")
        batch.drop_column("last_meldung_at")
        batch.drop_column("location_text")
        batch.drop_column("bottle_preset")
        batch.drop_column("planned_duration_min")
