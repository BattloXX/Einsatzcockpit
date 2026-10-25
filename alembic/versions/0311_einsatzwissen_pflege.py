"""Dokumentreferenzen mit Prüfangaben und tenantinternem Inhaltsabgleich."""

import sqlalchemy as sa

from alembic import op

revision = "0311"
down_revision = "0310"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("katplan_dokument", sa.Column("datei_hash", sa.String(64), nullable=True))
    op.create_index("ix_katplan_dokument_datei_hash", "katplan_dokument", ["datei_hash"])
    with op.batch_alter_table("katplan_anhang") as batch:
        batch.add_column(sa.Column("datei_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("pruefdatum", sa.Date(), nullable=True))
        batch.add_column(sa.Column("gueltig_bis", sa.Date(), nullable=True))
        batch.add_column(sa.Column("pruefintervall_monate", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("zustaendig_user_id", sa.BigInteger(), nullable=True))
        batch.create_foreign_key("fk_katplan_anhang_zustaendig", "user", ["zustaendig_user_id"], ["id"],
                                 ondelete="SET NULL")
        batch.create_index("ix_katplan_anhang_datei_hash", ["datei_hash"])


def downgrade() -> None:
    with op.batch_alter_table("katplan_anhang") as batch:
        batch.drop_index("ix_katplan_anhang_datei_hash")
        batch.drop_constraint("fk_katplan_anhang_zustaendig", type_="foreignkey")
        for name in ("zustaendig_user_id", "pruefintervall_monate", "gueltig_bis", "pruefdatum", "datei_hash"):
            batch.drop_column(name)
    op.drop_index("ix_katplan_dokument_datei_hash", table_name="katplan_dokument")
    with op.batch_alter_table("katplan_dokument") as batch:
        batch.drop_column("datei_hash")
