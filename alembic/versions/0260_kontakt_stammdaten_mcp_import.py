"""Extend central contacts with structured master data.

Revision ID: 0260
Revises: 0258
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0260"
# 0259 currently belongs to the independent road-closure worktree.  Keeping
# this revision on the last common committed ancestor makes both feature
# branches independently deployable; their integration requires Alembic's
# normal merge revision.
down_revision = "0258"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive migration: legacy free-text data stays readable and is never removed.
    for name, column in (
        ("anrede", sa.Column("anrede", sa.String(20))),
        ("titel_vor", sa.Column("titel_vor", sa.String(50))),
        ("titel_nach", sa.Column("titel_nach", sa.String(50))),
        ("gueltig_ab", sa.Column("gueltig_ab", sa.Date())),
        ("gueltig_bis", sa.Column("gueltig_bis", sa.Date())),
        ("zuletzt_geprueft", sa.Column("zuletzt_geprueft", sa.DateTime())),
        ("datenquelle", sa.Column("datenquelle", sa.String(100))),
        ("externe_quelle_id", sa.Column("externe_quelle_id", sa.String(150))),
        ("aktualisiert_ueber", sa.Column("aktualisiert_ueber", sa.String(20))),
    ):
        op.add_column("kontakt", column)
    for _name, column in (
        ("typ", sa.Column("typ", sa.String(20), nullable=False, server_default="sonstige")),
        ("verwendung", sa.Column("verwendung", sa.String(20), nullable=False, server_default="dienst")),
        ("whatsapp_eignung", sa.Column("whatsapp_eignung", sa.Boolean())),
        ("aktiv", sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true())),
    ):
        op.add_column("kontakt_telefon", column)

    op.create_table(
        "kontakt_email",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column("kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(200), nullable=False), sa.Column("typ", sa.String(20), nullable=False, server_default="sonstige"),
        sa.Column("label", sa.String(100)), sa.Column("bevorzugt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("sortierung", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_kontakt_email_org_kontakt", "kontakt_email", ["org_id", "kontakt_id"])
    op.execute("INSERT INTO kontakt_email (org_id, kontakt_id, email, typ, bevorzugt, aktiv, sortierung) SELECT org_id, id, email, 'sonstige', 1, 1, 0 FROM kontakt WHERE email IS NOT NULL AND email <> ''")
    op.create_table(
        "kontakt_organisation",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True), sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("kurzname", sa.String(100)), sa.Column("organisationstyp", sa.String(30), nullable=False, server_default="Sonstige"),
        sa.Column("uebergeordnete_organisation_id", sa.BigInteger(), sa.ForeignKey("kontakt_organisation.id", ondelete="SET NULL")),
        sa.Column("externe_id", sa.String(150)), sa.Column("quellenreferenz", sa.String(100)), sa.Column("website", sa.String(300)), sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("notizen", sa.Text()),
    )
    op.create_index("ix_kontakt_organisation_org_name", "kontakt_organisation", ["org_id", "name"])
    op.create_table(
        "kontakt_adresse",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True), sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column("kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="CASCADE")), sa.Column("organisation_id", sa.BigInteger(), sa.ForeignKey("kontakt_organisation.id", ondelete="SET NULL")),
        sa.Column("typ", sa.String(20), nullable=False, server_default="sonstige"), sa.Column("strasse", sa.String(200)), sa.Column("hausnummer", sa.String(30)), sa.Column("adresszusatz", sa.String(200)), sa.Column("plz", sa.String(20)), sa.Column("ort", sa.String(100)), sa.Column("bundesland", sa.String(100)), sa.Column("land", sa.String(2)), sa.Column("latitude", sa.String(30)), sa.Column("longitude", sa.String(30)), sa.Column("bevorzugt", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_kontakt_adresse_org_kontakt", "kontakt_adresse", ["org_id", "kontakt_id"])
    op.create_table(
        "kontakt_organisation_funktion",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True), sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column("kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False), sa.Column("organisation_id", sa.BigInteger(), sa.ForeignKey("kontakt_organisation.id", ondelete="CASCADE"), nullable=False), sa.Column("funktion", sa.String(150), nullable=False), sa.Column("funktionskategorie", sa.String(100)), sa.Column("ist_hauptfunktion", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("prioritaet", sa.Integer(), nullable=False, server_default="0"), sa.Column("gueltig_ab", sa.Date()), sa.Column("gueltig_bis", sa.Date()), sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("vertretung_kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="SET NULL")), sa.Column("erreichbarkeit", sa.Text()), sa.Column("bemerkung", sa.Text()),
    )
    op.create_index("ix_kontakt_org_funktion_kontakt", "kontakt_organisation_funktion", ["org_id", "kontakt_id"])
    op.create_table(
        "kontakt_import_batch",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True), sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")), sa.Column("quelle", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"), sa.Column("idempotency_key", sa.String(150)),
        sa.Column("request_json", sa.Text(), nullable=False), sa.Column("ergebnis_json", sa.Text(), nullable=False), sa.Column("erstellt_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "idempotency_key", name="uq_kontakt_import_batch_idempotency"),
    )


def downgrade() -> None:
    op.drop_table("kontakt_import_batch")
    op.drop_table("kontakt_organisation_funktion")
    op.drop_table("kontakt_adresse")
    op.drop_table("kontakt_organisation")
    op.drop_table("kontakt_email")
    for column in ("aktiv", "whatsapp_eignung", "verwendung", "typ"):
        op.drop_column("kontakt_telefon", column)
    for column in ("aktualisiert_ueber", "externe_quelle_id", "datenquelle", "zuletzt_geprueft", "gueltig_bis", "gueltig_ab", "titel_nach", "titel_vor", "anrede"):
        op.drop_column("kontakt", column)
