"""Georeferenzierte BSP-Overlays und Einsatz-Einstellungen."""

import sqlalchemy as sa

from alembic import op

revision = "0336"
down_revision = "0335"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "objekt_bsp_overlay",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("objekt_id", sa.BigInteger(), sa.ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dokument_id", sa.BigInteger(), sa.ForeignKey("objekt_dokument.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seite", sa.Integer(), nullable=False),
        sa.Column("georef_id", sa.BigInteger(), sa.ForeignKey("objekt_dokument_georef.id", ondelete="SET NULL"), nullable=True),
        sa.Column("georef_revision", sa.Integer(), nullable=True),
        sa.Column("dokument_hash", sa.String(64), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("plan_art", sa.String(30), server_default="lageplan", nullable=False),
        sa.Column("bereich", sa.String(200), nullable=True),
        sa.Column("geschoss", sa.String(100), nullable=True),
        sa.Column("ausschnitt_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), server_default="entwurf", nullable=False),
        sa.Column("freigabe_nr", sa.Integer(), server_default="0", nullable=False),
        sa.Column("freigegeben_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("freigegeben_am", sa.DateTime(), nullable=True),
        sa.Column("erstellt_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.Column("aktualisiert_am", sa.DateTime(), nullable=False),
        sa.Column("standard_sichtbar", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("standard_deckkraft", sa.Float(), server_default="0.6", nullable=False),
        sa.Column("empfohlen", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("autodruck", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("druck_deckkraft", sa.Float(), server_default="0.6", nullable=False),
        sa.Column("raster_pfad", sa.String(500), nullable=True),
        sa.Column("raster_sha256", sa.String(64), nullable=True),
        sa.Column("raster_bounds_json", sa.Text(), nullable=True),
        sa.Column("raster_breite", sa.Integer(), nullable=True),
        sa.Column("raster_hoehe", sa.Integer(), nullable=True),
        sa.Column("quelle_text", sa.Text(), nullable=True),
    )
    op.create_index("ix_objekt_bsp_overlay_org_id", "objekt_bsp_overlay", ["org_id"])
    op.create_index("ix_bsp_overlay_org_objekt", "objekt_bsp_overlay", ["org_id", "objekt_id"])
    op.create_table(
        "bsp_overlay_einsatz",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("overlay_id", sa.BigInteger(), sa.ForeignKey("objekt_bsp_overlay.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sichtbarkeit", sa.String(10), server_default="INHERIT", nullable=False),
        sa.Column("deckkraft", sa.Float(), nullable=True),
        sa.Column("geaendert_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("geaendert_am", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("incident_id", "overlay_id", name="uq_bsp_overlay_einsatz"),
    )
    op.create_index("ix_bsp_overlay_einsatz_org_id", "bsp_overlay_einsatz", ["org_id"])


def downgrade() -> None:
    op.drop_table("bsp_overlay_einsatz")
    op.drop_table("objekt_bsp_overlay")
