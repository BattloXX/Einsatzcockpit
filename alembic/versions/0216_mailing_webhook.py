"""Mailing webhook support. Revision ID: 0216; Revises: 0215."""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from alembic import op
revision="0216"; down_revision="0215"; branch_labels=None; depends_on=None
def upgrade():
    bind=op.get_bind(); insp=sa_inspect(bind); tables=set(insp.get_table_names())
    if "resend_webhook_secret_enc" not in {c["name"] for c in insp.get_columns("mailing_config")}:
        with op.batch_alter_table("mailing_config") as b: b.add_column(sa.Column("resend_webhook_secret_enc",sa.Text()))
    if "mailing_webhook_event" not in tables:
        op.create_table("mailing_webhook_event",
            sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),
            sa.Column("org_id",sa.BigInteger(),sa.ForeignKey("fire_dept.id",ondelete="SET NULL")),
            sa.Column("queue_item_id",sa.BigInteger(),sa.ForeignKey("mailing_queue_item.id",ondelete="SET NULL")),
            sa.Column("campaign_id",sa.BigInteger(),sa.ForeignKey("mailing_campaign.id",ondelete="SET NULL")),
            sa.Column("event_type",sa.String(50),nullable=False),sa.Column("resend_message_id",sa.String(200)),
            sa.Column("svix_id",sa.String(100),nullable=False,unique=True),sa.Column("payload_json",sa.Text(),nullable=False),
            sa.Column("received_at",sa.DateTime(),nullable=False),sa.Column("processed_at",sa.DateTime()),sa.Column("error_message",sa.Text()))
        op.create_index("ix_mailing_webhook_event_queue_item_id","mailing_webhook_event",["queue_item_id"])
        op.create_index("ix_mailing_webhook_event_resend_message_id","mailing_webhook_event",["resend_message_id"])
def downgrade(): pass
