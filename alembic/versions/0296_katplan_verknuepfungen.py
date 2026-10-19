"""Verknuepfungen und Beziehungen der Katastrophenplaene.

Revision ID: 0296
Revises: 0295
"""

import sqlalchemy as sa

from alembic import op

revision = "0296"
down_revision = "0295"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "katplan_link",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("element_id", sa.BigInteger(), sa.ForeignKey("katplan_element.id", ondelete="CASCADE"), nullable=True),
        sa.Column("ziel_typ", sa.String(30), nullable=False),
        sa.Column("ziel_id", sa.BigInteger(), nullable=False),
        sa.Column("rolle", sa.String(30), nullable=False),
        sa.Column("hinweis", sa.String(500), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.Column("erstellt_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("element_id", "ziel_typ", "ziel_id", "rolle"),
    )
    op.create_index("ix_katplan_link_org_id", "katplan_link", ["org_id"])
    op.create_index("ix_katplan_link_org_ziel", "katplan_link", ["org_id", "ziel_typ", "ziel_id"])
    op.create_index("ix_katplan_link_org_plan", "katplan_link", ["org_id", "plan_id"])
    op.create_table(
        "katplan_relation",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("von_element_id", sa.BigInteger(), sa.ForeignKey("katplan_element.id", ondelete="CASCADE"), nullable=False),
        sa.Column("zu_element_uuid", sa.String(36), nullable=False),
        sa.Column("art", sa.String(20), nullable=False),
        sa.Column("hinweis", sa.String(300), nullable=False),
        sa.UniqueConstraint("von_element_id", "zu_element_uuid", "art"),
    )
    op.create_index("ix_katplan_relation_org_id", "katplan_relation", ["org_id"])
    op.add_column("katplan_version_element", sa.Column("relationen_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("katplan_version_element", "relationen_json")
    op.drop_table("katplan_relation")
    op.drop_table("katplan_link")
