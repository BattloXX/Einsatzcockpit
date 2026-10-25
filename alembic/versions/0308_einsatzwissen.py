"""Einsatzwissen: additive Metadaten und benutzerbezogene letzte Zugriffe.

Bestehende Planarten, Inhalte und Freigabesnapshots bleiben unverändert.
"""

import sqlalchemy as sa

from alembic import op

revision = "0308"
down_revision = "0307"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, typ in (("kategorie", sa.String(30)), ("unterkategorie", sa.String(100)),
                      ("schlagwoerter_json", sa.Text()), ("kurzbeschreibung", sa.String(500))):
        op.add_column("katplan_plan", sa.Column(name, typ, nullable=True))
    op.create_table(
        "katplan_nutzung",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("zuletzt_am", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("org_id", "user_id", "plan_id", name="uq_katplan_nutzung"),
    )
    op.create_index("ix_katplan_nutzung_org_id", "katplan_nutzung", ["org_id"])


def downgrade() -> None:
    op.drop_table("katplan_nutzung")
    with op.batch_alter_table("katplan_plan") as batch:
        for name in ("kurzbeschreibung", "schlagwoerter_json", "unterkategorie", "kategorie"):
            batch.drop_column(name)
