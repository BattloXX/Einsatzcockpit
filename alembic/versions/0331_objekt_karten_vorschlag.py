"""Positionsqualitaet und getrennte Vorschlaege fuer Objekt-Lagekarten."""
import sqlalchemy as sa

from alembic import op

revision = "0331"
down_revision = "0330"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("objekt_karten_objekt") as batch:
        batch.add_column(sa.Column("position_status", sa.String(30), nullable=False, server_default="ungeprueft"))
        batch.add_column(sa.Column("quelle_dokument_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("quelle_seite", sa.SmallInteger(), nullable=True))
        batch.add_column(sa.Column("genauigkeit_m", sa.Float(), nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("geprueft_von", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("geprueft_am", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_objekt_karte_quelle", "objekt_dokument", ["quelle_dokument_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key("fk_objekt_karte_pruefer", "user", ["geprueft_von"], ["id"], ondelete="SET NULL")
    op.create_table(
        "objekt_karten_vorschlag",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("objekt_id", sa.BigInteger(), sa.ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False),
        sa.Column("basis_marker_id", sa.BigInteger(), sa.ForeignKey("objekt_karten_objekt.id", ondelete="SET NULL")),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("symbol_code", sa.String(40), nullable=False),
        sa.Column("label", sa.String(100)),
        sa.Column("rotation", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("lat", sa.Float()), sa.Column("lng", sa.Float()),
        sa.Column("geometry_json", sa.Text()),
        sa.Column("position_status", sa.String(30), nullable=False, server_default="vorgeschlagen"),
        sa.Column("position_methode", sa.String(40), nullable=False, server_default="unbekannt"),
        sa.Column("genauigkeit_m", sa.Float()), sa.Column("erkennungs_konfidenz", sa.Float()),
        sa.Column("quelle_dokument_id", sa.BigInteger(), sa.ForeignKey("objekt_dokument.id", ondelete="SET NULL")),
        sa.Column("quelle_seite", sa.SmallInteger()), sa.Column("quelle_ausschnitt", sa.Text()),
        sa.Column("quelle_dokument_hash", sa.String(64)), sa.Column("begruendung", sa.Text()),
        sa.Column("warnungen_json", sa.Text()), sa.Column("expected_revision", sa.Integer()),
        sa.Column("freigabe_status", sa.String(20), nullable=False, server_default="offen"),
        sa.Column("erstellt_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.Column("geprueft_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("geprueft_am", sa.DateTime()),
        sa.Column("quelle", sa.String(10), nullable=False, server_default="mcp"),
    )
    op.create_index("ix_objekt_karten_vorschlag_org_id", "objekt_karten_vorschlag", ["org_id"])
    op.create_index("ix_objekt_karten_vorschlag_org_objekt_status", "objekt_karten_vorschlag",
                    ["org_id", "objekt_id", "freigabe_status"])


def downgrade() -> None:
    op.drop_table("objekt_karten_vorschlag")
    with op.batch_alter_table("objekt_karten_objekt") as batch:
        batch.drop_constraint("fk_objekt_karte_pruefer", type_="foreignkey")
        batch.drop_constraint("fk_objekt_karte_quelle", type_="foreignkey")
        for name in ("geprueft_am", "geprueft_von", "revision", "genauigkeit_m", "quelle_seite",
                     "quelle_dokument_id", "position_status"):
            batch.drop_column(name)
