"""Unveraenderliche Katastrophenplan-Versionen.

Revision ID: 0295
Revises: 0294
"""

import sqlalchemy as sa

from alembic import op

revision = "0295"
down_revision = "0294"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "katplan_version",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("versionsnummer", sa.Integer(), nullable=False),
        sa.Column("freigegeben_am", sa.DateTime(), nullable=False),
        sa.Column("freigegeben_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("aenderungsnotiz", sa.Text(), nullable=True),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("inhalt_hash", sa.String(length=64), nullable=False),
        sa.Column("anzahl_elemente", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("plan_id", "versionsnummer"),
    )
    op.create_index("ix_katplan_version_org_id", "katplan_version", ['org_id'])
    op.create_index("ix_katplan_version_org_plan", "katplan_version", ['org_id', 'plan_id'])

    op.create_table(
        "katplan_version_element",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("version_id", sa.BigInteger(), sa.ForeignKey("katplan_version.id", ondelete="CASCADE"), nullable=False),
        sa.Column("element_uuid", sa.String(length=36), nullable=False),
        sa.Column("abschnitt_id", sa.BigInteger(), nullable=True),
        sa.Column("kennung", sa.String(length=30), nullable=True),
        sa.Column("typ", sa.String(length=30), nullable=False),
        sa.Column("titel", sa.String(length=200), nullable=False),
        sa.Column("beschreibung_html", sa.Text(), nullable=True),
        sa.Column("daten_json", sa.Text(), nullable=True),
        sa.Column("prioritaet", sa.Integer(), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=True),
        sa.Column("gueltig_bis", sa.Date(), nullable=True),
        sa.Column("pruefen_am", sa.Date(), nullable=True),
        sa.Column("vertraulich", sa.Boolean(), nullable=False),
        sa.Column("strasse", sa.String(length=200), nullable=True),
        sa.Column("hausnummer", sa.String(length=20), nullable=True),
        sa.Column("plz", sa.String(length=10), nullable=True),
        sa.Column("ort", sa.String(length=100), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("geometry_json", sa.Text(), nullable=True),
        sa.Column("geo_status", sa.String(length=12), nullable=False),
        sa.Column("herkunft", sa.String(length=20), nullable=False),
        sa.Column("quelle_dokument_id", sa.BigInteger(), nullable=True),
        sa.Column("quelle_seiten", sa.String(length=30), nullable=True),
        sa.Column("suchtext", sa.Text(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("phasen_json", sa.Text(), nullable=False),
        sa.Column("links_json", sa.Text(), nullable=False),
        sa.Column("anhaenge_json", sa.Text(), nullable=False),
        sa.Column("gsl_json", sa.Text(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("version_id", "element_uuid"),
    )
    op.create_index("ix_katplan_version_element_org_id", "katplan_version_element", ['org_id'])
    op.create_index("ix_katplan_version_element_org_uuid", "katplan_version_element", ['org_id', 'element_uuid'])
    op.create_index("ix_katplan_version_element_org_version", "katplan_version_element", ['org_id', 'version_id'])


def downgrade() -> None:
    op.drop_table("katplan_version_element")
    op.drop_table("katplan_version")
