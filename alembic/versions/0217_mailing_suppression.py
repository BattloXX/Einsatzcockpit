"""Mailing suppression support. Revision ID: 0217; Revises: 0216."""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from alembic import op
revision="0217"; down_revision="0216"; branch_labels=None; depends_on=None
def upgrade():
    bind=op.get_bind(); insp=sa_inspect(bind); tables=set(insp.get_table_names())
    if "suppressed_count" not in {c["name"] for c in insp.get_columns("mailing_campaign")}:
        with op.batch_alter_table("mailing_campaign") as b: b.add_column(sa.Column("suppressed_count",sa.Integer(),nullable=False,server_default="0"))
    if "mailing_suppression_entry" not in tables:
        op.create_table("mailing_suppression_entry",sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),
          sa.Column("org_id",sa.BigInteger(),sa.ForeignKey("fire_dept.id",ondelete="SET NULL")),sa.Column("email",sa.String(320),nullable=False),
          sa.Column("reason",sa.String(30),nullable=False),sa.Column("source_event_id",sa.BigInteger(),sa.ForeignKey("mailing_webhook_event.id",ondelete="SET NULL")),
          sa.Column("note",sa.Text()),sa.Column("created_by_user_id",sa.BigInteger(),sa.ForeignKey("user.id",ondelete="SET NULL")),sa.Column("created_at",sa.DateTime(),nullable=False),
          sa.UniqueConstraint("org_id","email",name="uq_mailing_suppression_email"))
        op.create_index("ix_mailing_suppression_entry_email","mailing_suppression_entry",["email"])
def downgrade(): pass
