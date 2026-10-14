"""Planspiel-Szenariokatalog.

Revision ID: 0285
Revises: 0284
"""

import sqlalchemy as sa

from alembic import op

revision = "0285"
down_revision = "0284"
branch_labels = None
depends_on = None


def _tenant_org_id() -> sa.Column:
    return sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True)


def upgrade() -> None:
    op.create_table(
        "planspiel_szenario",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("herkunft", sa.String(length=10), nullable=False),
        sa.Column("system_code", sa.String(length=40), nullable=True),
        sa.Column("system_version", sa.Integer(), nullable=True),
        sa.Column("org_geaendert", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("zufall_erlaubt", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("gewicht", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("aktive_version_id", sa.BigInteger(), nullable=True),
        sa.Column("archiviert_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("updated_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.UniqueConstraint("org_id", "code", name="uq_planspiel_szenario_org_code"),
    )
    op.create_table(
        "planspiel_szenario_version",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column(
            "szenario_id", sa.BigInteger(), sa.ForeignKey("planspiel_szenario.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("veroeffentlicht_at", sa.DateTime(), nullable=True),
        sa.Column("titel", sa.String(length=200), nullable=False),
        sa.Column("beschreibung", sa.Text(), nullable=True),
        sa.Column("kategorie", sa.String(length=20), nullable=False),
        sa.Column("stichwort_code", sa.String(length=10), nullable=False),
        sa.Column("stichwort_quelle", sa.String(length=60), nullable=True),
        sa.Column("alarmtext_template", sa.Text(), nullable=False),
        sa.Column("regie_geheimlage", sa.Text(), nullable=True),
        sa.Column("grundsituation", sa.Text(), nullable=True),
        sa.Column("ort_typen", sa.JSON(), nullable=False),
        sa.Column("geografie_bedingung", sa.JSON(), nullable=True),
        sa.Column("requires_existing_object", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_verified_bma", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("min_adressqualitaet", sa.String(length=1), nullable=True),
        sa.Column("schwierigkeit", sa.String(length=10), nullable=False),
        sa.Column("schlagworte", sa.Text(), nullable=True),
        sa.Column("lernziele", sa.Text(), nullable=True),
        sa.Column("ressourcenhinweis", sa.Text(), nullable=True),
        sa.Column("ausschlussregeln", sa.JSON(), nullable=True),
        sa.Column("gsl_modus", sa.String(length=20), nullable=True),
        sa.Column("gsl_verteilung", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.UniqueConstraint("szenario_id", "version", name="uq_planspiel_szenario_version"),
    )
    with op.batch_alter_table("planspiel_szenario") as batch_op:
        batch_op.create_foreign_key(
            "fk_planspiel_szenario_aktive_version", "planspiel_szenario_version", ["aktive_version_id"], ["id"],
            ondelete="SET NULL",
        )
    op.create_table(
        "planspiel_szenario_ereignis",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column(
            "version_id",
            sa.BigInteger(),
            sa.ForeignKey("planspiel_szenario_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("baustein_code", sa.String(length=40), nullable=True),
        sa.Column("ereignis_typ", sa.String(length=16), nullable=False),
        sa.Column("trigger_type", sa.String(length=24), nullable=False),
        sa.Column("offset_min_s", sa.Integer(), nullable=True),
        sa.Column("offset_max_s", sa.Integer(), nullable=True),
        sa.Column("trigger_param", sa.JSON(), nullable=True),
        sa.Column("wahrscheinlichkeit", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("max_fires", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("prioritaet", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sichtbarkeit", sa.String(length=12), nullable=False, server_default="teilnehmer"),
        sa.Column("ziel", sa.String(length=12), nullable=False, server_default="stelle"),
        sa.Column("payload_template", sa.Text(), nullable=False),
        sa.Column("voraussetzungen", sa.JSON(), nullable=True),
        sa.Column("abbruch", sa.JSON(), nullable=True),
        sa.Column("reihenfolge", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_table(
        "planspiel_ort_typ_zuordnung",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("quelle", sa.String(length=12), nullable=False),
        sa.Column("schluessel", sa.String(length=120), nullable=False),
        sa.Column("ort_typ", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("org_id", "quelle", "schluessel", "ort_typ", name="uq_planspiel_ort_typ_zuordnung"),
    )


def downgrade() -> None:
    op.drop_table("planspiel_ort_typ_zuordnung")
    op.drop_table("planspiel_szenario_ereignis")
    with op.batch_alter_table("planspiel_szenario") as batch_op:
        batch_op.drop_constraint("fk_planspiel_szenario_aktive_version", type_="foreignkey")
    op.drop_table("planspiel_szenario_version")
    op.drop_table("planspiel_szenario")
