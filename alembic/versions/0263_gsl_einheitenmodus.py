"""GSL-Einheitenmodus: Auftragsdaten und Geräteprotokoll.

Revision ID: 0263
Revises: 0262
"""

import sqlalchemy as sa

from alembic import op

revision = "0263"
down_revision = "0262"
branch_labels = None
depends_on = None


def _tenant_org_id() -> sa.Column:
    return sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True)


def upgrade() -> None:
    op.add_column("einheit_site_dispatch", sa.Column("auftrag", sa.Text(), nullable=True))
    op.add_column(
        "einheit_site_dispatch",
        sa.Column("einheit_status", sa.String(20), nullable=False, server_default=sa.text("'zugewiesen'")),
    )
    op.add_column("einheit_site_dispatch", sa.Column("status_at", sa.DateTime(), nullable=True))
    op.add_column("einheit_site_dispatch", sa.Column("bestaetigt_at", sa.DateTime(), nullable=True))
    op.add_column("einheit_site_dispatch", sa.Column("beendet_at", sa.DateTime(), nullable=True))
    op.add_column("einheit_site_dispatch", sa.Column("beendet_grund", sa.Text(), nullable=True))
    op.add_column("einheit_site_dispatch", sa.Column("reihenfolge", sa.Integer(), nullable=True))
    op.add_column(
        "einheit_site_dispatch", sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1"))
    )
    op.add_column("einheit_site_dispatch", sa.Column("geaendert_at", sa.DateTime(), nullable=True))
    op.add_column("einheit_site_dispatch", sa.Column("letzte_rueckmeldung_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_esd_einheit_aktiv", "einheit_site_dispatch", ["einheit_id", "withdrawn_at", "beendet_at"]
    )

    op.add_column("site_log_entry", sa.Column("einheit_id", sa.Integer(), nullable=True))
    op.add_column("site_log_entry", sa.Column("erfasst_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("site_log_entry") as batch_op:
        batch_op.create_foreign_key(
            "fk_site_log_entry_einheit_id", "lage_einheit", ["einheit_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_site_log_entry_einheit_id", "site_log_entry", ["einheit_id"])

    op.add_column("site_media", sa.Column("einheit_id", sa.Integer(), nullable=True))
    op.add_column("site_media", sa.Column("kommentar", sa.String(500), nullable=True))
    op.add_column("site_media", sa.Column("erfasst_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("site_media") as batch_op:
        batch_op.create_foreign_key(
            "fk_site_media_einheit_id", "lage_einheit", ["einheit_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_site_media_einheit_id", "site_media", ["einheit_id"])

    op.add_column("device_token", sa.Column("gsl_profil", sa.String(12), nullable=True))

    op.create_table(
        "einheit_aktion",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("client_uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("device_token_id", sa.BigInteger(), sa.ForeignKey("device_token.id", ondelete="SET NULL")),
        sa.Column("einheit_id", sa.Integer(), sa.ForeignKey("lage_einheit.id", ondelete="SET NULL")),
        sa.Column("dispatch_id", sa.Integer(), sa.ForeignKey("einheit_site_dispatch.id", ondelete="SET NULL")),
        sa.Column("aktion", sa.String(24), nullable=False),
        sa.Column("quelle", sa.String(12), nullable=False, server_default=sa.text("'tablet'")),
        sa.Column("ergebnis", sa.String(16), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=True),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("antwort_json", sa.Text(), nullable=True),
        sa.Column("erfasst_at", sa.DateTime(), nullable=True),
        sa.Column("empfangen_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_einheit_aktion_einheit_id", "einheit_aktion", ["einheit_id"])

    # Die Tabelle ist nicht tenant-gebunden; diese reine Datenmigration benötigt keinen Org-Bezug.
    op.execute(sa.text("UPDATE einheit_site_dispatch SET einheit_status = 'vor_ort' WHERE vor_ort_at IS NOT NULL"))
    op.execute(sa.text("UPDATE einheit_site_dispatch SET status_at = COALESCE(vor_ort_at, dispatched_at)"))


def downgrade() -> None:
    op.drop_index("ix_einheit_aktion_einheit_id", table_name="einheit_aktion")
    op.drop_table("einheit_aktion")

    op.drop_column("device_token", "gsl_profil")

    op.drop_index("ix_site_media_einheit_id", table_name="site_media")
    with op.batch_alter_table("site_media") as batch_op:
        batch_op.drop_constraint("fk_site_media_einheit_id", type_="foreignkey")
        batch_op.drop_column("erfasst_at")
        batch_op.drop_column("kommentar")
        batch_op.drop_column("einheit_id")

    op.drop_index("ix_site_log_entry_einheit_id", table_name="site_log_entry")
    with op.batch_alter_table("site_log_entry") as batch_op:
        batch_op.drop_constraint("fk_site_log_entry_einheit_id", type_="foreignkey")
        batch_op.drop_column("erfasst_at")
        batch_op.drop_column("einheit_id")

    op.drop_index("ix_esd_einheit_aktiv", table_name="einheit_site_dispatch")
    op.drop_column("einheit_site_dispatch", "letzte_rueckmeldung_at")
    op.drop_column("einheit_site_dispatch", "geaendert_at")
    op.drop_column("einheit_site_dispatch", "version")
    op.drop_column("einheit_site_dispatch", "reihenfolge")
    op.drop_column("einheit_site_dispatch", "beendet_grund")
    op.drop_column("einheit_site_dispatch", "beendet_at")
    op.drop_column("einheit_site_dispatch", "bestaetigt_at")
    op.drop_column("einheit_site_dispatch", "status_at")
    op.drop_column("einheit_site_dispatch", "einheit_status")
    op.drop_column("einheit_site_dispatch", "auftrag")
