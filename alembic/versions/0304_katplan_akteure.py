"""Organisationskatalog und Zuständigkeiten der Planelemente."""

import sqlalchemy as sa

from alembic import op

revision = "0304"
down_revision = "0303"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "katplan_akteur",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger, sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("schluessel", sa.String(40), nullable=False),
        sa.Column("bezeichnung", sa.String(80), nullable=False),
        sa.Column("farbe", sa.String(20), nullable=False, server_default="grau"),
        sa.Column("icon_name", sa.String(40), nullable=True),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("aktiv", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("org_id", "schluessel"),
    )
    op.create_index("ix_katplan_akteur_org_id", "katplan_akteur", ["org_id"])
    for tabelle in ("katplan_element", "katplan_version_element"):
        op.add_column(tabelle, sa.Column("akteure_json", sa.Text, nullable=True))


def downgrade() -> None:
    for tabelle in ("katplan_version_element", "katplan_element"):
        with op.batch_alter_table(tabelle) as batch:
            batch.drop_column("akteure_json")
    op.drop_index("ix_katplan_akteur_org_id", table_name="katplan_akteur")
    op.drop_table("katplan_akteur")
