"""Mailing API import batches. Revision ID: 0218; Revises: 0217."""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from alembic import op
revision="0218"; down_revision="0217"; branch_labels=None; depends_on=None
def upgrade():
    if "mailing_api_import_batch" not in set(sa_inspect(op.get_bind()).get_table_names()):
        op.create_table("mailing_api_import_batch",sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),
          sa.Column("org_id",sa.BigInteger(),sa.ForeignKey("fire_dept.id",ondelete="CASCADE"),nullable=False),
          sa.Column("list_id",sa.BigInteger(),sa.ForeignKey("mailing_recipient_list.id",ondelete="CASCADE"),nullable=False),
          sa.Column("external_key",sa.String(200),nullable=False),sa.Column("added_count",sa.Integer(),nullable=False,server_default="0"),
          sa.Column("skipped_count",sa.Integer(),nullable=False,server_default="0"),sa.Column("created_at",sa.DateTime(),nullable=False),
          sa.UniqueConstraint("org_id","list_id","external_key",name="uq_mailing_api_import_key"))
        op.create_index("ix_mailing_api_import_batch_org_id","mailing_api_import_batch",["org_id"])
        op.create_index("ix_mailing_api_import_batch_list_id","mailing_api_import_batch",["list_id"])
def downgrade(): pass
