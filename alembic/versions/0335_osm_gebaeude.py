"""Lokaler globaler OSM-Gebaeudebestand."""

import sqlalchemy as sa

from alembic import op

revision = "0335"
down_revision = "0334"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "osm_import_lauf",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("quelle", sa.String(40), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("gestartet_am", sa.DateTime(), nullable=False),
        sa.Column("beendet_am", sa.DateTime(), nullable=True),
        sa.Column("datenstand", sa.DateTime(), nullable=True),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in ("anzahl", "neu", "geaendert", "entfernt")),
        *(sa.Column(name, sa.Float(), nullable=True) for name in ("min_lat", "max_lat", "min_lng", "max_lng")),
        sa.Column("fehler_code", sa.String(60), nullable=True),
        sa.Column("fehler_text", sa.String(500), nullable=True),
    )
    op.create_index("ix_osm_import_lauf_quelle_status", "osm_import_lauf", ["quelle", "status"])
    op.create_table(
        "osm_gebaeude",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("lauf_id", sa.BigInteger(), sa.ForeignKey("osm_import_lauf.id", ondelete="CASCADE"), nullable=False),
        sa.Column("osm_way_id", sa.BigInteger(), nullable=False),
        sa.Column("ecken_json", sa.Text(), nullable=False),
        sa.Column("ecken_hash", sa.String(64), nullable=False),
        *(sa.Column(name, sa.Float(), nullable=False) for name in ("min_lat", "max_lat", "min_lng", "max_lng")),
    )
    op.create_index("ix_osm_gebaeude_lauf_position", "osm_gebaeude", ["lauf_id", "min_lat", "min_lng"])
    op.create_index("uq_osm_gebaeude_lauf_way", "osm_gebaeude", ["lauf_id", "osm_way_id"], unique=True)


def downgrade() -> None:
    op.drop_table("osm_gebaeude")
    op.drop_table("osm_import_lauf")
