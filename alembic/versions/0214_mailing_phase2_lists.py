"""Mailing Phase 2 dynamic lists. Revision ID: 0214; Revises: 0213."""
import sqlalchemy as sa
from sqlalchemy import inspect
from alembic import op
revision="0214"; down_revision="0213"; branch_labels=None; depends_on=None
def upgrade():
    i=inspect(op.get_bind())
    if "filter_json" not in {c["name"] for c in i.get_columns("mailing_recipient_list")}: op.add_column("mailing_recipient_list",sa.Column("filter_json",sa.Text()))
    if "member_tag" not in i.get_table_names(): op.create_table("member_tag",sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),sa.Column("org_id",sa.BigInteger(),sa.ForeignKey("fire_dept.id",ondelete="SET NULL"),nullable=True,index=True),sa.Column("name",sa.String(100),nullable=False),sa.Column("color",sa.String(30)),sa.UniqueConstraint("org_id","name",name="uq_member_tag_org_name"))
    if "member_tag_assignment" not in inspect(op.get_bind()).get_table_names(): op.create_table("member_tag_assignment",sa.Column("member_id",sa.BigInteger(),sa.ForeignKey("member.id",ondelete="CASCADE"),primary_key=True),sa.Column("tag_id",sa.BigInteger(),sa.ForeignKey("member_tag.id",ondelete="CASCADE"),primary_key=True),sa.Column("assigned_at",sa.DateTime(),nullable=False))
def downgrade(): pass
