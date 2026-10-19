"""Versionierte PDF-Quellen fuer Katastrophenplaene.

Revision ID: 0298
Revises: 0297
"""

import sqlalchemy as sa

from alembic import op

revision = "0298"
down_revision = "0297"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "katplan_dokument",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column("uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("titel", sa.String(200), nullable=False),
        sa.Column("dateiname_original", sa.String(255), nullable=False),
        sa.Column("pfad", sa.String(500), nullable=False),
        sa.Column("groesse_bytes", sa.BigInteger(), nullable=False),
        sa.Column("belegt_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("seitenzahl", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="neu"),
        sa.Column("fehler_text", sa.String(500)),
        sa.Column("dokument_gruppe_id", sa.BigInteger(), sa.ForeignKey("katplan_dokument.id", ondelete="SET NULL")),
        sa.Column("versionsnummer", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ist_aktuelle_version", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("herausgeber", sa.String(200), nullable=False, server_default=""),
        sa.Column("stand", sa.Date()),
        sa.Column("archiviert", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hochgeladen_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("hochgeladen_am", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_katplan_dokument_org_id", "katplan_dokument", ["org_id"])
    op.create_index("ix_katplan_dokument_org_plan", "katplan_dokument", ["org_id", "plan_id"])
    op.create_table(
        "katplan_dokument_seite",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column("dokument_id", sa.BigInteger(), sa.ForeignKey("katplan_dokument.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seiten_nr", sa.Integer(), nullable=False),
        sa.Column("einzel_pdf_pfad", sa.String(500), nullable=False),
        sa.Column("bild_pfad", sa.String(500)),
        sa.Column("thumb_pfad", sa.String(500)),
        sa.Column("volltext", sa.Text()),
        sa.Column("text_quelle", sa.String(10), nullable=False, server_default="none"),
        sa.Column("titel", sa.String(200)),
    )
    op.create_index("ix_katplan_dokument_seite_org_id", "katplan_dokument_seite", ["org_id"])
    op.create_index("ix_katplan_dokument_seite_org_plan", "katplan_dokument_seite", ["org_id", "plan_id"])
    op.create_index("ix_katplan_dokument_seite_org_dok_nr", "katplan_dokument_seite", ["org_id", "dokument_id", "seiten_nr"])


def downgrade() -> None:
    op.drop_table("katplan_dokument_seite")
    op.drop_table("katplan_dokument")
