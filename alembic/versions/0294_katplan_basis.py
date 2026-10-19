"""Katastrophenplan-Basisdatenmodell.

Revision ID: 0294
Revises: 0293
"""

import sqlalchemy as sa

from alembic import op

revision = "0294"
down_revision = "0293"
branch_labels = None
depends_on = None


def _tenant_org_id() -> sa.Column:
    return sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True)


def upgrade() -> None:
    op.create_table(
        "katplan_plan",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("titel", sa.String(200), nullable=False),
        sa.Column("beschreibung_html", sa.Text(), nullable=True),
        sa.Column("gefahrenarten_json", sa.Text(), nullable=True),
        sa.Column("geltungsbereich", sa.String(300), nullable=True),
        sa.Column("verantwortliche_stelle", sa.String(200), nullable=True),
        sa.Column("verantwortlich_kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="entwurf"),
        sa.Column("aktuelle_version_id", sa.BigInteger(), nullable=True),
        sa.Column("wiedervorlage_am", sa.Date(), nullable=True),
        sa.Column("wiedervorlage_erinnert_am", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.Column("aktualisiert_am", sa.DateTime(), nullable=False),
        sa.Column("erstellt_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("aktualisiert_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_katplan_plan_org_status", "katplan_plan", ["org_id", "status"])

    op.create_table(
        "katplan_abschnitt",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("katplan_abschnitt.id", ondelete="CASCADE"), nullable=True),
        sa.Column("titel", sa.String(200), nullable=False),
        sa.Column("beschreibung_html", sa.Text(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_katplan_abschnitt_org_plan", "katplan_abschnitt", ["org_id", "plan_id"])

    op.create_table(
        "katplan_phase",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("beschreibung", sa.Text(), nullable=True),
        sa.Column("ausloeser", sa.Text(), nullable=True),
        sa.Column("farbe", sa.String(20), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "katplan_element",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("uuid", sa.String(36), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("abschnitt_id", sa.BigInteger(), sa.ForeignKey("katplan_abschnitt.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kennung", sa.String(30), nullable=True),
        sa.Column("typ", sa.String(30), nullable=False),
        sa.Column("titel", sa.String(200), nullable=False),
        sa.Column("beschreibung_html", sa.Text(), nullable=True),
        sa.Column("daten_json", sa.Text(), nullable=True),
        sa.Column("prioritaet", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.Column("status", sa.String(20), nullable=False, server_default="entwurf"),
        sa.Column("tags_json", sa.Text(), nullable=True),
        sa.Column("gueltig_bis", sa.Date(), nullable=True),
        sa.Column("pruefen_am", sa.Date(), nullable=True),
        sa.Column("vertraulich", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("strasse", sa.String(200), nullable=True),
        sa.Column("hausnummer", sa.String(20), nullable=True),
        sa.Column("plz", sa.String(10), nullable=True),
        sa.Column("ort", sa.String(100), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("geometry_json", sa.Text(), nullable=True),
        sa.Column("geo_status", sa.String(12), nullable=False, server_default="keine"),
        sa.Column("herkunft", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("quelle_dokument_id", sa.BigInteger(), nullable=True),
        sa.Column("quelle_seiten", sa.String(30), nullable=True),
        sa.Column("gsl_vorlage", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gsl_auftragsart", sa.String(12), nullable=True),
        sa.Column("gsl_prioritaet", sa.Integer(), nullable=True),
        sa.Column("gsl_ressourcentypen_json", sa.Text(), nullable=True),
        sa.Column("gsl_dauer_min", sa.Integer(), nullable=True),
        sa.Column("gsl_voraussetzungen", sa.Text(), nullable=True),
        sa.Column("suchtext", sa.Text(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.Column("aktualisiert_am", sa.DateTime(), nullable=False),
        sa.Column("erstellt_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("aktualisiert_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("plan_id", "uuid"),
        sa.UniqueConstraint("plan_id", "kennung"),
    )
    op.create_index("ix_katplan_element_org_plan_sort", "katplan_element", ["org_id", "plan_id", "sort"])
    op.create_index("ix_katplan_element_org_typ", "katplan_element", ["org_id", "typ"])

    op.create_table(
        "katplan_element_phase",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("element_id", sa.BigInteger(), sa.ForeignKey("katplan_element.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phase_id", sa.BigInteger(), sa.ForeignKey("katplan_phase.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("element_id", "phase_id"),
    )

    op.create_table(
        "katplan_aenderung",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("element_id", sa.BigInteger(), nullable=True),
        sa.Column("entitaet", sa.String(20), nullable=False),
        sa.Column("aktion", sa.String(20), nullable=False),
        sa.Column("feld", sa.String(60), nullable=True),
        sa.Column("alt", sa.Text(), nullable=True),
        sa.Column("neu", sa.Text(), nullable=True),
        sa.Column("quelle", sa.String(20), nullable=False, server_default="ui"),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("zeit", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_katplan_aenderung_org_plan_zeit", "katplan_aenderung", ["org_id", "plan_id", "zeit"])

    op.create_table(
        "katplan_pruefung",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=True),
        sa.Column("aktion", sa.String(20), nullable=False),
        sa.Column("kommentar", sa.Text(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("zeit", sa.DateTime(), nullable=False),
    )

    with op.batch_alter_table("org_settings") as batch_op:
        batch_op.add_column(sa.Column("katplan_modul_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch_op:
        batch_op.drop_column("katplan_modul_aktiv")
    # Abhaengige Tabellen zuerst entfernen; ihre FKs und Indizes entfallen gemeinsam.
    op.drop_table("katplan_pruefung")
    op.drop_table("katplan_aenderung")
    op.drop_table("katplan_element_phase")
    op.drop_table("katplan_element")
    op.drop_table("katplan_phase")
    op.drop_table("katplan_abschnitt")
    op.drop_table("katplan_plan")
