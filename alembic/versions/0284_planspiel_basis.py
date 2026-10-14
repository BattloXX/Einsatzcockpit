"""Planspiel-Basisdatenmodell.

Revision ID: 0284
Revises: 0283
"""

import sqlalchemy as sa

from alembic import op

revision = "0284"
down_revision = "0283"
branch_labels = None
depends_on = None


def _tenant_org_id() -> sa.Column:
    return sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True)


def upgrade() -> None:
    op.create_table(
        "planspiel_run",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("titel", sa.String(length=200), nullable=False),
        sa.Column("art", sa.String(length=20), nullable=False),
        sa.Column("erzeugung", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="entwurf"),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("tempo", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("konfig", sa.JSON(), nullable=True),
        sa.Column("szenario_snapshot", sa.JSON(), nullable=True),
        sa.Column("kanal_freigaben", sa.JSON(), nullable=True),
        sa.Column("leiter_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="SET NULL")),
        sa.Column("lage_id", sa.Integer(), sa.ForeignKey("major_incident.id", ondelete="SET NULL")),
        sa.Column("gestartet_at", sa.DateTime(), nullable=True),
        sa.Column("pausiert_seit", sa.DateTime(), nullable=True),
        sa.Column("pausiert_summe_s", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("gen_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("folge_aktiv", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("naechste_gen_sim_s", sa.Float(), nullable=True),
        sa.Column("gen_seq", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_stellen", sa.Integer(), nullable=True),
        sa.Column("max_laufzeit_s", sa.Integer(), nullable=True),
        sa.Column("steuer_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("worker_lease_until", sa.DateTime(), nullable=True),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("letzter_fehler", sa.Text(), nullable=True),
        sa.Column("beendet_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
    )
    op.create_index("ix_planspiel_run_org_status", "planspiel_run", ["org_id", "status"])

    op.create_table(
        "planspiel_run_ereignis",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("planspiel_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("quelle", sa.String(length=12), nullable=False),
        sa.Column("baustein_code", sa.String(length=40), nullable=True),
        sa.Column("ziel_typ", sa.String(length=12), nullable=True),
        sa.Column("ziel_id", sa.BigInteger(), nullable=True),
        sa.Column("trigger_type", sa.String(length=24), nullable=False),
        sa.Column("trigger_param", sa.JSON(), nullable=True),
        sa.Column("faellig_sim_s", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="geplant"),
        sa.Column("idempotenz_key", sa.String(length=120), nullable=False, unique=True),
        sa.Column("versuche", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("ausgefuehrt_at", sa.DateTime(), nullable=True),
        sa.Column("ausgefuehrt_sim_s", sa.Float(), nullable=True),
        sa.Column("ergebnis_ref", sa.JSON(), nullable=True),
        sa.Column("fehler", sa.Text(), nullable=True),
        sa.Column("sichtbarkeit", sa.String(length=12), nullable=False, server_default="teilnehmer"),
        sa.Column("text", sa.Text(), nullable=True),
        sa.UniqueConstraint("run_id", "seq"),
    )
    op.create_index(
        "ix_planspiel_run_ereignis_run_status_faellig",
        "planspiel_run_ereignis",
        ["run_id", "status", "faellig_sim_s"],
    )

    op.create_table(
        "planspiel_ziel",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("planspiel_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("typ", sa.String(length=12), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="SET NULL")),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("incident_site.id", ondelete="SET NULL")),
        sa.Column("marker_id", sa.Integer(), sa.ForeignKey("cross_site_marker.id", ondelete="SET NULL")),
        sa.Column("szenario_code", sa.String(length=40), nullable=True),
        sa.Column("stichwort_code", sa.String(length=10), nullable=True),
        sa.Column("adress_snapshot", sa.JSON(), nullable=True),
        sa.Column("ist_duplikat_von", sa.BigInteger(), sa.ForeignKey("planspiel_ziel.id", ondelete="SET NULL")),
        sa.Column("erzeugt_sim_s", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "seq"),
    )
    op.create_table(
        "planspiel_mitglied",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("planspiel_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rolle", sa.String(length=12), nullable=False),
        sa.UniqueConstraint("run_id", "user_id"),
    )
    op.create_table(
        "planspiel_beobachtung",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("planspiel_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("sim_s", sa.Float(), nullable=True),
        sa.Column("lernziel", sa.String(length=200), nullable=True),
        sa.Column("kategorie", sa.String(length=40), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("bewertung", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    with op.batch_alter_table("incident") as batch_op:
        batch_op.add_column(sa.Column("planspiel_run_id", sa.BigInteger(), nullable=True))
        batch_op.create_foreign_key(
            "fk_incident_planspiel_run_id", "planspiel_run", ["planspiel_run_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_incident_planspiel_run_id", ["planspiel_run_id"])
    with op.batch_alter_table("major_incident") as batch_op:
        batch_op.add_column(sa.Column("planspiel_run_id", sa.BigInteger(), nullable=True))
        batch_op.create_foreign_key(
            "fk_major_incident_planspiel_run_id", "planspiel_run", ["planspiel_run_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_major_incident_planspiel_run_id", ["planspiel_run_id"])
    with op.batch_alter_table("org_settings") as batch_op:
        batch_op.add_column(sa.Column("planspiel_modul_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("planspiel_gebiet_geojson", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("planspiel_gebiet_radius_m", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("planspiel_max_laufende_runs", sa.Integer(), nullable=False, server_default=sa.text("2"))
        )
        batch_op.add_column(
            sa.Column("planspiel_max_stellen_je_minute", sa.Integer(), nullable=False, server_default=sa.text("6"))
        )
        batch_op.add_column(sa.Column("planspiel_gsl_gewichte", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("planspiel_aufbewahrung_tage", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch_op:
        batch_op.drop_column("planspiel_aufbewahrung_tage")
        batch_op.drop_column("planspiel_gsl_gewichte")
        batch_op.drop_column("planspiel_max_stellen_je_minute")
        batch_op.drop_column("planspiel_max_laufende_runs")
        batch_op.drop_column("planspiel_gebiet_radius_m")
        batch_op.drop_column("planspiel_gebiet_geojson")
        batch_op.drop_column("planspiel_modul_aktiv")
    with op.batch_alter_table("major_incident") as batch_op:
        batch_op.drop_constraint("fk_major_incident_planspiel_run_id", type_="foreignkey")
        batch_op.drop_index("ix_major_incident_planspiel_run_id")
        batch_op.drop_column("planspiel_run_id")
    with op.batch_alter_table("incident") as batch_op:
        batch_op.drop_constraint("fk_incident_planspiel_run_id", type_="foreignkey")
        batch_op.drop_index("ix_incident_planspiel_run_id")
        batch_op.drop_column("planspiel_run_id")
    op.drop_table("planspiel_beobachtung")
    op.drop_table("planspiel_mitglied")
    op.drop_table("planspiel_ziel")
    op.drop_index("ix_planspiel_run_ereignis_run_status_faellig", table_name="planspiel_run_ereignis")
    op.drop_table("planspiel_run_ereignis")
    op.drop_index("ix_planspiel_run_org_status", table_name="planspiel_run")
    op.drop_table("planspiel_run")
