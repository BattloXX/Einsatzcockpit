"""Anhaenge fuer Katastrophenplaene.

Revision ID: 0297
Revises: 0296
"""

import sqlalchemy as sa

from alembic import op

revision = "0297"
down_revision = "0296"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "katplan_anhang",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("abschnitt_id", sa.BigInteger(), sa.ForeignKey("katplan_abschnitt.id", ondelete="SET NULL")),
        sa.Column("element_id", sa.BigInteger(), sa.ForeignKey("katplan_element.id", ondelete="SET NULL")),
        sa.Column("art", sa.String(20), nullable=False),
        sa.Column("dateiname", sa.String(255)),
        sa.Column("pfad", sa.String(500)),
        sa.Column("thumb_pfad", sa.String(500)),
        sa.Column("mime", sa.String(100)),
        sa.Column("groesse_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("breite", sa.Integer()),
        sa.Column("hoehe", sa.Integer()),
        sa.Column("url", sa.String(1000)),
        sa.Column("objekt_dokument_id", sa.BigInteger()),
        sa.Column("seiten", sa.String(30)),
        sa.Column("beschriftung", sa.String(300), nullable=False, server_default=""),
        sa.Column("urheber", sa.String(150), nullable=False, server_default=""),
        sa.Column("aufnahme_datum", sa.Date()),
        sa.Column("vertraulich", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("geloescht_am", sa.DateTime()),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.Column("erstellt_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
    )
    op.create_index("ix_katplan_anhang_org_id", "katplan_anhang", ["org_id"])
    op.create_index("ix_katplan_anhang_org_plan", "katplan_anhang", ["org_id", "plan_id"])
    op.create_index("ix_katplan_anhang_org_element", "katplan_anhang", ["org_id", "element_id"])


def downgrade() -> None:
    op.drop_table("katplan_anhang")
