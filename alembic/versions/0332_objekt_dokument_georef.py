"""Versionierte Georeferenzen; hoechste Revision je Dokument/Seite ist aktuell."""

import sqlalchemy as sa

from alembic import op

revision = "0332"
down_revision = "0331"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "objekt_dokument_georef",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("objekt_id", sa.BigInteger(), sa.ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "dokument_id", sa.BigInteger(), sa.ForeignKey("objekt_dokument.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seite", sa.Integer(), nullable=False),
        sa.Column("dokument_hash", sa.String(64), nullable=False),
        sa.Column("referenzpunkte_json", sa.Text(), nullable=False),
        sa.Column("modell", sa.String(20), nullable=False),
        sa.Column("parameter_json", sa.Text()),
        sa.Column("rmse_m", sa.Float()),
        sa.Column("max_fehler_m", sa.Float()),
        sa.Column("residuen_json", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("ablehngrund", sa.Text()),
        sa.Column("erstellt_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.Column("geprueft_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("geprueft_am", sa.DateTime()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("org_id", "dokument_id", "seite", "revision", name="uq_objekt_georef_revision"),
    )
    op.create_index("ix_objekt_dokument_georef_org_id", "objekt_dokument_georef", ["org_id"])
    with op.batch_alter_table("objekt_karten_vorschlag") as batch:
        batch.add_column(sa.Column("georef_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("bild_x", sa.Float(), nullable=True))
        batch.add_column(sa.Column("bild_y", sa.Float(), nullable=True))
        batch.create_foreign_key(
            "fk_vorschlag_georef", "objekt_dokument_georef", ["georef_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("objekt_karten_vorschlag") as batch:
        batch.drop_constraint("fk_vorschlag_georef", type_="foreignkey")
        for name in ("bild_y", "bild_x", "georef_id"):
            batch.drop_column(name)
    op.drop_table("objekt_dokument_georef")
