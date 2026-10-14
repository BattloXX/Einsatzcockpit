"""MCP-Upload einer GSL-Einsatzstelle zuordnen.

Revision ID: 0281_mcp_upload_site
Revises: 0280_dispatch_abgezogen
"""
from alembic import op
import sqlalchemy as sa

revision = "0281_mcp_upload_site"
down_revision = "0280_dispatch_abgezogen"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mcp_upload") as batch_op:
        batch_op.add_column(sa.Column("site_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_mcp_upload_site_id", "incident_site", ["site_id"], ["id"], ondelete="CASCADE")


def downgrade():
    with op.batch_alter_table("mcp_upload") as batch_op:
        batch_op.drop_constraint("fk_mcp_upload_site_id", type_="foreignkey")
        batch_op.drop_column("site_id")
