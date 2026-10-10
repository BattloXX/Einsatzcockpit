"""GSL-Ressourcenkarte: Basisdaten und Journalschema.

Revision ID: 0265
Revises: 0264
"""

import sqlalchemy as sa

from alembic import op

revision = "0265"
down_revision = "0264"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lage_einheit_leader", sa.Column("rolle", sa.String(16), nullable=False,
                                                    server_default=sa.text("'fuehrer'")))
    op.add_column("lage_einheit_leader", sa.Column("phone", sa.String(30), nullable=True))
    op.add_column("lage_einheit_leader", sa.Column("phone_e164", sa.String(20), nullable=True))
    op.add_column("lage_einheit_leader", sa.Column("phone_version", sa.Integer(), nullable=False,
                                                    server_default=sa.text("1")))
    op.add_column("lage_einheit_leader", sa.Column("ende_grund", sa.String(24), nullable=True))
    op.add_column("lage_einheit_leader", sa.Column("ende_von", sa.BigInteger(), nullable=True))
    with op.batch_alter_table("lage_einheit_leader") as batch_op:
        batch_op.create_foreign_key("fk_lel_ende_von", "user", ["ende_von"], ["id"], ondelete="SET NULL")
    op.create_index("ix_lel_einheit_aktiv", "lage_einheit_leader", ["einheit_id", "rolle", "end_at"])

    op.add_column("lage_einheit", sa.Column("funkrufname", sa.String(40), nullable=True))
    op.add_column("lage_einheit", sa.Column("bereitstellungsraum", sa.String(200), nullable=True))
    op.add_column("lage_einheit", sa.Column("status_at", sa.DateTime(), nullable=True))
    # LageEinheit hat keine org_id. Zeilenweises Setzen vermeidet ein ungescoptes
    # Bulk-Update und bestimmt tatsächlich den größten vorhandenen Zeitpunkt.
    bind = op.get_bind()
    einheiten = sa.Table("lage_einheit", sa.MetaData(), autoload_with=bind)
    rows = bind.execute(sa.select(
        einheiten.c.id, einheiten.c.released_at, einheiten.c.committed_at,
        einheiten.c.arrived_at, einheiten.c.requested_at, einheiten.c.added_at,
    )).mappings()
    for row in rows:
        status_at = max(
            value for value in (
                row["released_at"], row["committed_at"], row["arrived_at"],
                row["requested_at"], row["added_at"],
            ) if value is not None
        )
        bind.execute(einheiten.update().where(einheiten.c.id == row["id"]).values(status_at=status_at))

    op.add_column("vehicle_master", sa.Column("funkrufname", sa.String(40), nullable=True))

    op.add_column("einheit_site_dispatch", sa.Column("withdrawn_by", sa.BigInteger(), nullable=True))
    op.add_column("einheit_site_dispatch", sa.Column("withdrawn_author", sa.String(120), nullable=True))
    op.add_column("einheit_site_dispatch", sa.Column("withdrawn_grund", sa.String(500), nullable=True))
    with op.batch_alter_table("einheit_site_dispatch") as batch_op:
        batch_op.create_foreign_key("fk_esd_withdrawn_by", "user", ["withdrawn_by"], ["id"], ondelete="SET NULL")

    op.add_column("lage_journal_entry", sa.Column("einheit_id", sa.Integer(), nullable=True))
    op.add_column("lage_journal_entry", sa.Column("site_id", sa.Integer(), nullable=True))
    op.add_column("lage_journal_entry", sa.Column("ereignis_typ", sa.String(24), nullable=True))
    op.add_column("lage_journal_entry", sa.Column("quelle", sa.String(12), nullable=True))
    op.add_column("lage_journal_entry", sa.Column("storniert_at", sa.DateTime(), nullable=True))
    op.add_column("lage_journal_entry", sa.Column("storniert_von", sa.String(120), nullable=True))
    op.add_column("lage_journal_entry", sa.Column("storno_grund", sa.String(300), nullable=True))
    with op.batch_alter_table("lage_journal_entry") as batch_op:
        batch_op.create_foreign_key("fk_lje_einheit_id", "lage_einheit", ["einheit_id"], ["id"], ondelete="SET NULL")
        batch_op.create_foreign_key("fk_lje_site_id", "incident_site", ["site_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_lage_journal_entry_einheit_id", "lage_journal_entry", ["einheit_id"])


def downgrade() -> None:
    with op.batch_alter_table("lage_journal_entry") as batch_op:
        batch_op.drop_constraint("fk_lje_site_id", type_="foreignkey")
        batch_op.drop_constraint("fk_lje_einheit_id", type_="foreignkey")
    op.drop_index("ix_lage_journal_entry_einheit_id", table_name="lage_journal_entry")
    with op.batch_alter_table("lage_journal_entry") as batch_op:
        batch_op.drop_column("storno_grund")
        batch_op.drop_column("storniert_von")
        batch_op.drop_column("storniert_at")
        batch_op.drop_column("quelle")
        batch_op.drop_column("ereignis_typ")
        batch_op.drop_column("site_id")
        batch_op.drop_column("einheit_id")

    with op.batch_alter_table("einheit_site_dispatch") as batch_op:
        batch_op.drop_constraint("fk_esd_withdrawn_by", type_="foreignkey")
        batch_op.drop_column("withdrawn_grund")
        batch_op.drop_column("withdrawn_author")
        batch_op.drop_column("withdrawn_by")

    op.drop_column("vehicle_master", "funkrufname")
    op.drop_column("lage_einheit", "status_at")
    op.drop_column("lage_einheit", "bereitstellungsraum")
    op.drop_column("lage_einheit", "funkrufname")

    op.drop_index("ix_lel_einheit_aktiv", table_name="lage_einheit_leader")
    with op.batch_alter_table("lage_einheit_leader") as batch_op:
        batch_op.drop_constraint("fk_lel_ende_von", type_="foreignkey")
        batch_op.drop_column("ende_von")
        batch_op.drop_column("ende_grund")
        batch_op.drop_column("phone_version")
        batch_op.drop_column("phone_e164")
        batch_op.drop_column("phone")
        batch_op.drop_column("rolle")
