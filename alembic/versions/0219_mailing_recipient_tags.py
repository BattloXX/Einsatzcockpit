"""Mailing-Empfaengergruppen. Revision ID: 0219; Revises: 0218."""

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "0219"
down_revision = "0218"
branch_labels = None
depends_on = None


def upgrade():
    if "mailing_recipient_list_entry_tag" not in set(sa_inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "mailing_recipient_list_entry_tag",
            sa.Column(
                "entry_id",
                sa.BigInteger(),
                sa.ForeignKey("mailing_recipient_list_entry.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "tag_id",
                sa.BigInteger(),
                sa.ForeignKey("member_tag.id", ondelete="CASCADE"),
                primary_key=True,
            ),
        )


def downgrade():
    op.drop_table("mailing_recipient_list_entry_tag")
