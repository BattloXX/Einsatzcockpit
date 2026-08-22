"""Tabellen des Mailing-Moduls.

Revision ID: 0210
Revises: 0209
"""

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "0210"
down_revision = "0209"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa_inspect(op.get_bind()).get_table_names())

    def create(name, *cols, **kw):
        if name not in existing:
            op.create_table(name, *cols, **kw)

    def tenant():
        return sa.Column(
            "org_id",
            sa.BigInteger(),
            sa.ForeignKey("fire_dept.id", ondelete="SET NULL"),
            nullable=True,
        )

    def times():
        return (sa.Column("created_at", sa.DateTime(), nullable=False),)

    create(
        "mailing_template",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        tenant(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        *times(),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    create(
        "mailing_recipient_list",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        tenant(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("auto_source", sa.String(50)),
        sa.Column("description", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *times(),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    create(
        "mailing_recipient_list_entry",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        tenant(),
        sa.Column(
            "list_id", sa.BigInteger(), sa.ForeignKey("mailing_recipient_list.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200)),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("org_id", "list_id", "email", name="uq_mailing_list_email"),
    )
    create(
        "mailing_campaign",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        tenant(),
        sa.Column("template_id", sa.BigInteger(), sa.ForeignKey("mailing_template.id"), nullable=False),
        sa.Column("recipient_list_id", sa.BigInteger(), sa.ForeignKey("mailing_recipient_list.id"), nullable=False),
        sa.Column("source_incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="SET NULL")),
        sa.Column("subject_override", sa.String(500)),
        sa.Column("reply_to_override", sa.String(320)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        *times(),
        sa.Column("queued_at", sa.DateTime()),
        sa.Column("sent_at", sa.DateTime()),
    )
    create(
        "mailing_queue_item",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        tenant(),
        sa.Column(
            "campaign_id", sa.BigInteger(), sa.ForeignKey("mailing_campaign.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("resend_message_id", sa.String(200)),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", sa.DateTime()),
        *times(),
        sa.Column("sent_at", sa.DateTime()),
    )
    create(
        "mailing_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resend_api_key_enc", sa.Text()),
        sa.Column("from_addr", sa.String(320)),
        sa.Column("reply_to_default", sa.String(320)),
        sa.Column("sender_display_name", sa.String(200)),
        *times(),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    insp = sa_inspect(op.get_bind())
    if "mailing_queue_item" in insp.get_table_names() and "ix_mailing_queue_dispatch" not in {
        i["name"] for i in insp.get_indexes("mailing_queue_item")
    }:
        op.create_index("ix_mailing_queue_dispatch", "mailing_queue_item", ["org_id", "status", "next_attempt_at"])


def downgrade() -> None:
    existing = set(sa_inspect(op.get_bind()).get_table_names())
    for name in (
        "mailing_queue_item",
        "mailing_campaign",
        "mailing_recipient_list_entry",
        "mailing_recipient_list",
        "mailing_template",
        "mailing_config",
    ):
        if name in existing:
            op.drop_table(name)
