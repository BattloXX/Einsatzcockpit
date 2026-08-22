"""Mailing Phase 2 list merge. Revision ID: 0215; Revises: 0214."""
import sqlalchemy as sa
from sqlalchemy import inspect
from alembic import op
revision="0215"; down_revision="0214"; branch_labels=None; depends_on=None
def upgrade():
    if "mailing_campaign_recipient_list" not in inspect(op.get_bind()).get_table_names():
        op.create_table("mailing_campaign_recipient_list",sa.Column("campaign_id",sa.BigInteger(),sa.ForeignKey("mailing_campaign.id",ondelete="CASCADE"),primary_key=True),sa.Column("recipient_list_id",sa.BigInteger(),sa.ForeignKey("mailing_recipient_list.id",ondelete="CASCADE"),primary_key=True))
    bind=op.get_bind(); bind.execute(sa.text("INSERT INTO mailing_campaign_recipient_list (campaign_id, recipient_list_id) SELECT id, recipient_list_id FROM mailing_campaign WHERE recipient_list_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM mailing_campaign_recipient_list x WHERE x.campaign_id=mailing_campaign.id AND x.recipient_list_id=mailing_campaign.recipient_list_id)"))
    col=next(c for c in inspect(bind).get_columns("mailing_campaign") if c["name"]=="recipient_list_id")
    if not col["nullable"]:
        with op.batch_alter_table("mailing_campaign") as b: b.alter_column("recipient_list_id",existing_type=sa.BigInteger(),nullable=True)
def downgrade(): pass
