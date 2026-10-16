"""Planspiel-Vorlagen.

Revision ID: 0287
Revises: 0286
"""

import sqlalchemy as sa

from alembic import op

revision = "0287"
down_revision = "0286"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planspiel_vorlage",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True),
        sa.Column("titel", sa.String(length=200), nullable=False),
        sa.Column("beschreibung", sa.Text(), nullable=True),
        sa.Column("art", sa.String(length=10), nullable=False),
        sa.Column("konfig", sa.JSON(), nullable=False),
        sa.Column("seed_fixiert", sa.BigInteger(), nullable=True),
        sa.Column("quelle_run_id", sa.BigInteger(), sa.ForeignKey("planspiel_run.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("zuletzt_verwendet_at", sa.DateTime(), nullable=True),
        sa.Column("verwendet_anzahl", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_planspiel_vorlage_org_art", "planspiel_vorlage", ["org_id", "art"])


def downgrade() -> None:
    op.drop_index("ix_planspiel_vorlage_org_art", table_name="planspiel_vorlage")
    op.drop_table("planspiel_vorlage")
