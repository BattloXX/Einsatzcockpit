"""Grundschema fuer zentrale, organisationsweite Kontakte.

Revision ID: 0238
Revises: 0237
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0238"
down_revision = "0237"
branch_labels = None
depends_on = None


def _tenant_columns(*columns: sa.Column) -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "org_id", sa.BigInteger(),
            sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True,
        ),
        *columns,
    ]


def upgrade() -> None:
    op.create_table(
        "kontakt",
        *_tenant_columns(
            sa.Column("typ", sa.String(length=20), nullable=False, server_default="person"),
            sa.Column("anzeigename", sa.String(length=150), nullable=False),
            sa.Column("vorname", sa.String(length=100), nullable=True),
            sa.Column("nachname", sa.String(length=100), nullable=True),
            sa.Column("funktion", sa.String(length=150), nullable=True),
            sa.Column("organisation", sa.String(length=200), nullable=True),
            sa.Column("email", sa.String(length=200), nullable=True),
            sa.Column("erreichbarkeit", sa.Text(), nullable=True),
            sa.Column("notizen", sa.Text(), nullable=True),
            sa.Column("bild_pfad", sa.String(length=500), nullable=True),
            sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("archiviert", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("erstellt_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("aktualisiert_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("erstellt_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
            sa.Column("aktualisiert_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        ),
    )
    op.create_index("ix_kontakt_org_id", "kontakt", ["org_id"])
    op.create_index("ix_kontakt_org_anzeigename", "kontakt", ["org_id", "anzeigename"])

    op.create_table(
        "kontakt_telefon",
        *_tenant_columns(
            sa.Column("kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False),
            sa.Column("nummer", sa.String(length=100), nullable=False),
            sa.Column("nummer_normalisiert", sa.String(length=100), nullable=False),
            sa.Column("label", sa.String(length=100), nullable=True),
            sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("bevorzugt", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("sms_eignung", sa.Boolean(), nullable=True),
        ),
    )
    op.create_index("ix_kontakt_telefon_org_id", "kontakt_telefon", ["org_id"])
    op.create_index("ix_kontakt_telefon_org_kontakt", "kontakt_telefon", ["org_id", "kontakt_id"])

    op.create_table(
        "kontakt_kategorie",
        *_tenant_columns(sa.Column("name", sa.String(length=100), nullable=False)),
        sa.UniqueConstraint("org_id", "name", name="uq_kontakt_kategorie_org_name"),
    )
    op.create_index("ix_kontakt_kategorie_org_id", "kontakt_kategorie", ["org_id"])

    op.create_table(
        "kontakt_kategorie_zuordnung",
        *_tenant_columns(
            sa.Column("kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kategorie_id", sa.BigInteger(), sa.ForeignKey("kontakt_kategorie.id", ondelete="CASCADE"), nullable=False),
        ),
        sa.UniqueConstraint("kontakt_id", "kategorie_id", name="uq_kontakt_kategorie_zuordnung"),
    )
    op.create_index("ix_kontakt_kategorie_zuordnung_org_id", "kontakt_kategorie_zuordnung", ["org_id"])

    op.create_table(
        "kontakt_anhang",
        *_tenant_columns(
            sa.Column("kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False),
            sa.Column("dateiname", sa.String(length=255), nullable=False),
            sa.Column("medientyp", sa.String(length=100), nullable=False),
            sa.Column("groesse_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("speicher_pfad", sa.String(length=500), nullable=False),
            sa.Column("hochgeladen_am", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("hochgeladen_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        ),
    )
    op.create_index("ix_kontakt_anhang_org_id", "kontakt_anhang", ["org_id"])
    op.create_index("ix_kontakt_anhang_org_kontakt", "kontakt_anhang", ["org_id", "kontakt_id"])

    op.create_table(
        "kontakt_externe_referenz",
        *_tenant_columns(
            sa.Column("kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False),
            sa.Column("quelle", sa.String(length=50), nullable=False),
            sa.Column("quelle_kontext", sa.String(length=100), nullable=True),
            sa.Column("extern_id", sa.String(length=150), nullable=False),
        ),
        sa.UniqueConstraint("org_id", "quelle", "quelle_kontext", "extern_id", name="uq_kontakt_externe_referenz"),
    )
    op.create_index("ix_kontakt_externe_referenz_org_id", "kontakt_externe_referenz", ["org_id"])

    op.create_table(
        "objekt_kontakt_freigabe",
        *_tenant_columns(
            sa.Column("objekt_kontakt_id", sa.BigInteger(), sa.ForeignKey("objekt_kontakt.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kanal", sa.String(length=10), nullable=False),
            sa.Column("ziel_wert", sa.String(length=200), nullable=False),
            sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.false()),
        ),
        sa.UniqueConstraint("objekt_kontakt_id", "kanal", "ziel_wert", name="uq_objekt_kontakt_freigabe"),
    )
    op.create_index("ix_objekt_kontakt_freigabe_org_id", "objekt_kontakt_freigabe", ["org_id"])
    op.create_index(
        "ix_objekt_kontakt_freigabe_org_kontakt", "objekt_kontakt_freigabe", ["org_id", "objekt_kontakt_id"]
    )

    with op.batch_alter_table("objekt_kontakt") as batch_op:
        batch_op.add_column(sa.Column("kontakt_id", sa.BigInteger(), nullable=True))
        batch_op.create_foreign_key(
            "fk_objekt_kontakt_kontakt_id", "kontakt", ["kontakt_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("objekt_kontakt") as batch_op:
        batch_op.drop_constraint("fk_objekt_kontakt_kontakt_id", type_="foreignkey")
        batch_op.drop_column("kontakt_id")

    op.drop_table("objekt_kontakt_freigabe")
    op.drop_table("kontakt_externe_referenz")
    op.drop_table("kontakt_anhang")
    op.drop_table("kontakt_kategorie_zuordnung")
    op.drop_table("kontakt_kategorie")
    op.drop_table("kontakt_telefon")
    op.drop_table("kontakt")
