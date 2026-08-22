"""Mailing Phase 2 tracking. Revision ID: 0211; Revises: 0210."""
import sqlalchemy as sa
from sqlalchemy import inspect
from alembic import op
revision="0211"; down_revision="0210"; branch_labels=None; depends_on=None
def _add(table, column):
    if column.name not in {c["name"] for c in inspect(op.get_bind()).get_columns(table)}: op.add_column(table, column)
def upgrade():
    for c in (sa.Column("open_count",sa.Integer(),nullable=False,server_default="0"),sa.Column("click_count",sa.Integer(),nullable=False,server_default="0"),sa.Column("track_opens",sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column("track_clicks",sa.Boolean(),nullable=False,server_default=sa.true())): _add("mailing_campaign",c)
    for c in (sa.Column("opened_at",sa.DateTime()),sa.Column("open_count",sa.Integer(),nullable=False,server_default="0"),sa.Column("first_clicked_at",sa.DateTime()),sa.Column("click_count",sa.Integer(),nullable=False,server_default="0")): _add("mailing_queue_item",c)
    if "mailing_link_click" not in inspect(op.get_bind()).get_table_names():
        op.create_table("mailing_link_click",sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),sa.Column("org_id",sa.BigInteger(),sa.ForeignKey("fire_dept.id",ondelete="SET NULL"),nullable=True,index=True),sa.Column("queue_item_id",sa.BigInteger(),sa.ForeignKey("mailing_queue_item.id",ondelete="CASCADE"),nullable=False,index=True),sa.Column("url",sa.Text(),nullable=False),sa.Column("clicked_at",sa.DateTime(),nullable=False),sa.Column("ip",sa.String(64)))
def downgrade(): pass
