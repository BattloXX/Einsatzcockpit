"""Dauerhafte SMS-Auftraege mit Claim, Generation und Gateway-Freigabe."""

import sqlalchemy as sa

from alembic import op

revision = "0313"
down_revision = "0312"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("api_message_recipient", sa.Column("dispatch_job_id", sa.String(32), nullable=True))
    op.create_index("ix_api_message_recipient_dispatch_job_id", "api_message_recipient", ["dispatch_job_id"])
    op.add_column("sms_gateway_token", sa.Column("sms_protocol", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("sms_log_recipient", sa.Column("dispatch_job_id", sa.String(32), nullable=True))
    op.add_column("sms_log_recipient", sa.Column("dispatch_status", sa.String(20), nullable=True))
    op.create_index("ix_sms_log_recipient_dispatch_job_id", "sms_log_recipient", ["dispatch_job_id"])
    op.create_table(
        "sms_dispatch_job",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.String(32)),
        sa.Column("claimed_by", sa.String(96)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("providers", sa.JSON(), nullable=False),
        sa.Column("provider_index", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(20)),
        sa.Column("preferred_gateway_id", sa.BigInteger()),
        sa.Column("gateway_id", sa.BigInteger()),
        sa.Column("connection_id", sa.String(32)),
        sa.Column("protocol", sa.Integer(), nullable=False),
        sa.Column("may_have_sent", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime()),
        sa.Column("attempted_at", sa.DateTime()),
        sa.Column("accepted_at", sa.DateTime()),
        sa.Column("delivery_status", sa.String(20), nullable=False),
        sa.Column("error", sa.String(200)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.UniqueConstraint("org_id", "job_key", name="uq_sms_dispatch_job_key"),
    )
    op.create_index("ix_sms_dispatch_due", "sms_dispatch_job", ["status", "next_attempt_at", "lease_until"])
    op.create_index("ix_sms_dispatch_gateway", "sms_dispatch_job", ["org_id", "gateway_id", "status"])
    op.create_table(
        "sms_dispatch_attempt",
        sa.Column("job_id", sa.String(32), sa.ForeignKey("sms_dispatch_job.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("generation", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(20)),
        sa.Column("gateway_id", sa.BigInteger()),
        sa.Column("protocol", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.String(32)),
        sa.Column("grant_route_id", sa.String(32)),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("error", sa.String(200)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_table(
        "sms_dispatch_cursor",
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_gateway_id", sa.BigInteger()),
    )


def downgrade():
    op.drop_index("ix_api_message_recipient_dispatch_job_id", table_name="api_message_recipient")
    op.drop_column("api_message_recipient", "dispatch_job_id")
    op.drop_table("sms_dispatch_cursor")
    op.drop_table("sms_dispatch_attempt")
    op.drop_table("sms_dispatch_job")
    op.drop_index("ix_sms_log_recipient_dispatch_job_id", table_name="sms_log_recipient")
    op.drop_column("sms_log_recipient", "dispatch_status")
    op.drop_column("sms_log_recipient", "dispatch_job_id")
    op.drop_column("sms_gateway_token", "sms_protocol")
