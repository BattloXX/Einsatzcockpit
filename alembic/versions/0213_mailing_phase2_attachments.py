"""Mailing Phase 2 attachments. Revision ID: 0213; Revises: 0212."""
import sqlalchemy as sa
from sqlalchemy import inspect
from alembic import op
revision="0213"; down_revision="0212"; branch_labels=None; depends_on=None
def upgrade():
    if "mailing_campaign_attachment" not in inspect(op.get_bind()).get_table_names():
        op.create_table("mailing_campaign_attachment",sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),sa.Column("org_id",sa.BigInteger(),sa.ForeignKey("fire_dept.id",ondelete="SET NULL"),nullable=True,index=True),sa.Column("campaign_id",sa.BigInteger(),sa.ForeignKey("mailing_campaign.id",ondelete="CASCADE"),nullable=False,index=True),sa.Column("original_filename",sa.String(255),nullable=False),sa.Column("storage_path",sa.String(1000),nullable=False),sa.Column("mime_type",sa.String(100),nullable=False),sa.Column("bytes",sa.BigInteger(),nullable=False),sa.Column("uploaded_by_user_id",sa.BigInteger(),sa.ForeignKey("user.id",ondelete="SET NULL")),sa.Column("created_at",sa.DateTime(),nullable=False))
def downgrade(): pass
