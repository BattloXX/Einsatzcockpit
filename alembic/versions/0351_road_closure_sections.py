"""Strassensperren mit mehreren Abschnitten."""
import sqlalchemy as sa

from alembic import op

revision = "0351"
down_revision = "0350"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "road_closure_section",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("road_closure_id", sa.BigInteger(), sa.ForeignKey("road_closure.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("street", sa.String(200), nullable=True),
        sa.Column("from_text", sa.String(200), nullable=True),
        sa.Column("to_text", sa.String(200), nullable=True),
        sa.Column("direction", sa.String(20), nullable=True),
        sa.Column("geometry_geojson", sa.Text(), nullable=True),
        sa.Column("geometry_status", sa.String(20), nullable=False, server_default="missing"),
        sa.Column("geometry_quality", sa.String(10), nullable=True),
        sa.Column("geometry_meta_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_road_closure_section_org_id", "road_closure_section", ["org_id"])
    op.create_index(
        "ix_road_closure_section_closure_position", "road_closure_section", ["road_closure_id", "position"]
    )
    op.execute(sa.text("""
        INSERT INTO road_closure_section (
            org_id, road_closure_id, position, street, from_text, to_text, direction,
            geometry_geojson, geometry_status, geometry_quality, geometry_meta_json, created_at, updated_at
        )
        SELECT org_id, id, 0, street, from_text, to_text, direction,
            geometry_geojson, geometry_status, geometry_quality, geometry_meta_json, created_at, updated_at
        FROM road_closure
        WHERE (street IS NOT NULL AND street <> '') OR (geometry_geojson IS NOT NULL AND geometry_geojson <> '')
    """))


def downgrade() -> None:
    op.drop_table("road_closure_section")
