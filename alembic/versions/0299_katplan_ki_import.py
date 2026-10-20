"""Review-Warteschlange fuer den KI-Planimport.

Revision ID: 0299
Revises: 0298
"""

import sqlalchemy as sa

from alembic import op

revision = "0299"
down_revision = "0298"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_settings", sa.Column("katplan_ki_import_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("katplan_dokument", sa.Column("ki_status", sa.String(20)))
    op.add_column("katplan_dokument", sa.Column("ki_fehler", sa.String(300)))
    op.add_column("katplan_dokument", sa.Column("ki_analysiert_am", sa.DateTime()))
    op.create_table(
        "katplan_import_vorschlag",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL")),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dokument_id", sa.BigInteger(), sa.ForeignKey("katplan_dokument.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seite_id", sa.BigInteger(), sa.ForeignKey("katplan_dokument_seite.id", ondelete="SET NULL")),
        sa.Column("seiten_nr", sa.Integer(), nullable=False),
        sa.Column("reihenfolge", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("typ", sa.String(30), nullable=False),
        sa.Column("titel", sa.String(200), nullable=False),
        sa.Column("beschreibung_html", sa.Text()),
        sa.Column("daten_json", sa.Text()),
        sa.Column("kapitel", sa.String(200)),
        sa.Column("phase", sa.String(100)),
        sa.Column("prioritaet", sa.Integer()),
        sa.Column("strasse", sa.String(200)),
        sa.Column("hausnummer", sa.String(20)),
        sa.Column("plz", sa.String(10)),
        sa.Column("ort", sa.String(100)),
        sa.Column("kontakte_json", sa.Text()),
        sa.Column("objekt_name", sa.String(200)),
        sa.Column("objekt_id", sa.BigInteger()),
        sa.Column("begruendung", sa.String(300)),
        sa.Column("status", sa.String(20), nullable=False, server_default="offen"),
        sa.Column("element_id", sa.BigInteger()),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.Column("entschieden_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("entschieden_am", sa.DateTime()),
    )
    op.create_index("ix_katplan_import_vorschlag_org_id", "katplan_import_vorschlag", ["org_id"])
    op.create_index("ix_katplan_import_org_plan_status", "katplan_import_vorschlag", ["org_id", "plan_id", "status"])
    op.create_index("ix_katplan_import_org_dokument", "katplan_import_vorschlag", ["org_id", "dokument_id"])


def downgrade() -> None:
    op.drop_table("katplan_import_vorschlag")
    with op.batch_alter_table("katplan_dokument") as batch:
        batch.drop_column("ki_analysiert_am")
        batch.drop_column("ki_fehler")
        batch.drop_column("ki_status")
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("katplan_ki_import_enabled")
