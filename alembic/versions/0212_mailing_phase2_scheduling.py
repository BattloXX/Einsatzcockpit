"""Mailing Phase 2 scheduling. Revision ID: 0212; Revises: 0211."""
import sqlalchemy as sa
from sqlalchemy import inspect
from alembic import op
revision="0212"; down_revision="0211"; branch_labels=None; depends_on=None
def upgrade():
    names={c["name"] for c in inspect(op.get_bind()).get_columns("mailing_campaign")}
    for c in (sa.Column("scheduled_at",sa.DateTime()),sa.Column("cancelled_at",sa.DateTime()),sa.Column("max_attempts_override",sa.Integer())):
        if c.name not in names: op.add_column("mailing_campaign",c)
def downgrade(): pass
