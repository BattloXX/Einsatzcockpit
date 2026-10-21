"""Planart fuer Einsatz- und Katastrophenplaene.

Revision ID: 0302
Revises: 0301
"""

import sqlalchemy as sa

from alembic import op

revision = "0302"
down_revision = "0301"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("katplan_plan", sa.Column(
        "planart", sa.String(30), nullable=False, server_default="katastrophenplan"))
    op.create_index("ix_katplan_plan_org_planart", "katplan_plan", ["org_id", "planart"])


def downgrade() -> None:
    op.drop_index("ix_katplan_plan_org_planart", table_name="katplan_plan")
    with op.batch_alter_table("katplan_plan") as batch:
        batch.drop_column("planart")
