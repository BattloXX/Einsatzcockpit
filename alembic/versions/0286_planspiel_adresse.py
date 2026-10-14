"""Planspiel-Adresspool.

Revision ID: 0286
Revises: 0285
"""

import sqlalchemy as sa

from alembic import op

revision = "0286"
down_revision = "0285"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planspiel_adresse",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True),
        sa.Column("quelle", sa.String(length=16), nullable=False),
        sa.Column("quell_id", sa.String(length=64), nullable=True),
        sa.Column("objekt_id", sa.BigInteger(), sa.ForeignKey("objekt.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bezeichnung", sa.String(length=200), nullable=True),
        sa.Column("strasse", sa.String(length=200), nullable=False),
        sa.Column("hausnr", sa.String(length=20), nullable=True),
        sa.Column("plz", sa.String(length=10), nullable=True),
        sa.Column("ort", sa.String(length=100), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False), sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("qualitaet", sa.String(length=1), nullable=False), sa.Column("ort_typen", sa.JSON(), nullable=False),
        sa.Column("merkmale", sa.JSON(), nullable=True),
        sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("zuletzt_verwendet_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("org_id", "quelle", "quell_id", name="uq_planspiel_adresse_org_quelle_quell_id"),
    )
    op.create_index("ix_planspiel_adresse_org_aktiv", "planspiel_adresse", ["org_id", "aktiv"])


def downgrade() -> None:
    op.drop_index("ix_planspiel_adresse_org_aktiv", table_name="planspiel_adresse")
    op.drop_table("planspiel_adresse")
