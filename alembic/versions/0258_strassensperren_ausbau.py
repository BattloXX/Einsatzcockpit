"""Straßensperren: Zusatzdaten, Dokumente und MCP-Upload-Zweck.

Revision ID: 0258
Revises: 0257
"""

import sqlalchemy as sa

from alembic import op

revision = "0258"
down_revision = "0257"
branch_labels = None
depends_on = None


def _tenant_org_id() -> sa.Column:
    return sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True)


def upgrade() -> None:
    op.add_column("road_closure", sa.Column("city", sa.String(120), nullable=True))
    op.add_column("road_closure", sa.Column("reference_number", sa.String(120), nullable=True))
    op.add_column("road_closure", sa.Column("exceptions", sa.Text(), nullable=True))
    op.add_column("road_closure", sa.Column("authority", sa.String(200), nullable=True))
    op.add_column("road_closure", sa.Column("superseded_by_id", sa.BigInteger(), nullable=True))
    op.add_column("road_closure", sa.Column("geometry_quality", sa.String(10), nullable=True))
    op.add_column("road_closure", sa.Column("geometry_meta_json", sa.Text(), nullable=True))
    with op.batch_alter_table("road_closure") as batch_op:
        batch_op.create_foreign_key(
            "fk_road_closure_superseded_by_id", "road_closure", ["superseded_by_id"], ["id"], ondelete="SET NULL"
        )
    op.create_table(
        "road_closure_document",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("road_closure_id", sa.BigInteger(), sa.ForeignKey("road_closure.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("source", sa.String(10), nullable=False, server_default="ui"),
        sa.Column("uploaded_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_road_closure_document_road_closure_id", "road_closure_document", ["road_closure_id"])
    with op.batch_alter_table("mcp_upload") as batch_op:
        batch_op.alter_column("objekt_id", existing_type=sa.BigInteger(), nullable=True)
        batch_op.add_column(sa.Column("road_closure_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("zweck", sa.String(20), nullable=False, server_default="objekt"))
        batch_op.create_foreign_key(
            "fk_mcp_upload_road_closure_id", "road_closure", ["road_closure_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    with op.batch_alter_table("mcp_upload") as batch_op:
        batch_op.drop_constraint("fk_mcp_upload_road_closure_id", type_="foreignkey")
        batch_op.drop_column("zweck")
        batch_op.drop_column("road_closure_id")
        batch_op.alter_column("objekt_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_index("ix_road_closure_document_road_closure_id", table_name="road_closure_document")
    op.drop_table("road_closure_document")
    with op.batch_alter_table("road_closure") as batch_op:
        batch_op.drop_constraint("fk_road_closure_superseded_by_id", type_="foreignkey")
    op.drop_column("road_closure", "geometry_meta_json")
    op.drop_column("road_closure", "geometry_quality")
    op.drop_column("road_closure", "superseded_by_id")
    op.drop_column("road_closure", "authority")
    op.drop_column("road_closure", "exceptions")
    op.drop_column("road_closure", "reference_number")
    op.drop_column("road_closure", "city")
