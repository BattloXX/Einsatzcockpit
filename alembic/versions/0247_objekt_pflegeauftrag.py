"""Add external object maintenance assignments.

Revision ID: 0247
Revises: 0246
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0247"
down_revision = "0246"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "objekt_pflegeauftrag",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("objekt_id", sa.BigInteger(), nullable=False),
        sa.Column("kontakt_id", sa.BigInteger(), nullable=False),
        sa.Column("arbeitskopie_id", sa.BigInteger(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("bereiche_json", sa.Text(), nullable=False),
        sa.Column("auftrag_text", sa.Text(), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(), nullable=True),
        sa.Column("erstellt_von_id", sa.BigInteger(), nullable=True),
        sa.Column("gesendet_am", sa.DateTime(), nullable=True),
        sa.Column("gueltig_bis", sa.DateTime(), nullable=False),
        sa.Column("erster_zugriff_am", sa.DateTime(), nullable=True),
        sa.Column("letzter_zugriff_am", sa.DateTime(), nullable=True),
        sa.Column("abgeschlossen_am", sa.DateTime(), nullable=True),
        sa.Column("freigegeben_am", sa.DateTime(), nullable=True),
        sa.Column("freigegeben_von_id", sa.BigInteger(), nullable=True),
        sa.Column("verworfen_am", sa.DateTime(), nullable=True),
        sa.Column("verworfen_von_id", sa.BigInteger(), nullable=True),
        sa.Column("widerrufen_am", sa.DateTime(), nullable=True),
        sa.Column("widerrufen_von_id", sa.BigInteger(), nullable=True),
        sa.Column("nacharbeit_am", sa.DateTime(), nullable=True),
        sa.Column("nacharbeit_text", sa.Text(), nullable=True),
        sa.Column("erinnerung_1_am", sa.DateTime(), nullable=True),
        sa.Column("erinnerung_2_am", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["erstellt_von_id"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["freigegeben_von_id"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["kontakt_id"], ["kontakt.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["objekt_id"], ["objekt.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["arbeitskopie_id"], ["objekt.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["verworfen_von_id"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["widerrufen_von_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_objekt_pflegeauftrag_org_objekt_status", "objekt_pflegeauftrag", ["org_id", "objekt_id", "status"])
    op.create_table(
        "objekt_pflege_abschnitt",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("pflegeauftrag_id", sa.BigInteger(), nullable=False),
        sa.Column("bereich", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("bestaetigt_am", sa.DateTime(), nullable=True),
        sa.Column("geaendert_am", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["pflegeauftrag_id"], ["objekt_pflegeauftrag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("pflegeauftrag_id", "bereich", name="uq_objekt_pflege_abschnitt"),
    )
    op.create_table(
        "objekt_pflege_ereignis",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("pflegeauftrag_id", sa.BigInteger(), nullable=False),
        sa.Column("typ", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("kontakt_id", sa.BigInteger(), nullable=True),
        sa.Column("metadaten_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["kontakt_id"], ["kontakt.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pflegeauftrag_id"], ["objekt_pflegeauftrag.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_objekt_pflege_ereignis_org_auftrag_ts", "objekt_pflege_ereignis", ["org_id", "pflegeauftrag_id", "erstellt_am"])
    op.create_table(
        "kontakt_aenderungsvorschlag",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("pflegeauftrag_id", sa.BigInteger(), nullable=False),
        sa.Column("kontakt_id", sa.BigInteger(), nullable=False),
        sa.Column("basis_version", sa.Integer(), nullable=False),
        sa.Column("diff_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("erstellt_am", sa.DateTime(), nullable=True),
        sa.Column("geprueft_am", sa.DateTime(), nullable=True),
        sa.Column("geprueft_von_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["geprueft_von_id"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["kontakt_id"], ["kontakt.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pflegeauftrag_id"], ["objekt_pflegeauftrag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column("objekt", sa.Column("letzte_bestaetigung_am", sa.DateTime(), nullable=True))
    op.add_column("objekt", sa.Column("letzte_bestaetigung_kontakt_id", sa.BigInteger(), nullable=True))
    op.add_column("objekt", sa.Column("letzte_bestaetigung_pflegeauftrag_id", sa.BigInteger(), nullable=True))
    ist_mysql = op.get_bind().dialect.name == "mysql"
    if ist_mysql:
        op.create_foreign_key("fk_objekt_letzte_bestaetigung_kontakt", "objekt", "kontakt", ["letzte_bestaetigung_kontakt_id"], ["id"], ondelete="SET NULL")
        op.create_foreign_key("fk_objekt_letzte_bestaetigung_auftrag", "objekt", "objekt_pflegeauftrag", ["letzte_bestaetigung_pflegeauftrag_id"], ["id"], ondelete="SET NULL")
    op.add_column("objekt_change", sa.Column("quelle", sa.String(length=20), nullable=False, server_default="intern"))
    op.add_column("objekt_change", sa.Column("pflegeauftrag_id", sa.BigInteger(), nullable=True))
    op.add_column("objekt_change", sa.Column("kontakt_id", sa.BigInteger(), nullable=True))
    if ist_mysql:
        op.create_foreign_key("fk_objekt_change_pflegeauftrag", "objekt_change", "objekt_pflegeauftrag", ["pflegeauftrag_id"], ["id"], ondelete="SET NULL")
        op.create_foreign_key("fk_objekt_change_kontakt", "objekt_change", "kontakt", ["kontakt_id"], ["id"], ondelete="SET NULL")
    op.alter_column("objekt_change", "quelle", server_default=None)


def downgrade() -> None:
    ist_mysql = op.get_bind().dialect.name == "mysql"
    if ist_mysql:
        op.drop_constraint("fk_objekt_change_kontakt", "objekt_change", type_="foreignkey")
        op.drop_constraint("fk_objekt_change_pflegeauftrag", "objekt_change", type_="foreignkey")
    op.drop_column("objekt_change", "kontakt_id")
    op.drop_column("objekt_change", "pflegeauftrag_id")
    op.drop_column("objekt_change", "quelle")
    if ist_mysql:
        op.drop_constraint("fk_objekt_letzte_bestaetigung_auftrag", "objekt", type_="foreignkey")
        op.drop_constraint("fk_objekt_letzte_bestaetigung_kontakt", "objekt", type_="foreignkey")
    op.drop_column("objekt", "letzte_bestaetigung_pflegeauftrag_id")
    op.drop_column("objekt", "letzte_bestaetigung_kontakt_id")
    op.drop_column("objekt", "letzte_bestaetigung_am")
    op.drop_table("kontakt_aenderungsvorschlag")
    op.drop_index("ix_objekt_pflege_ereignis_org_auftrag_ts", table_name="objekt_pflege_ereignis")
    op.drop_table("objekt_pflege_ereignis")
    op.drop_table("objekt_pflege_abschnitt")
    op.drop_index("ix_objekt_pflegeauftrag_org_objekt_status", table_name="objekt_pflegeauftrag")
    op.drop_table("objekt_pflegeauftrag")
