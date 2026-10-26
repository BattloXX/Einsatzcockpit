"""Workeruebergreifende SMS-/Linux-Presence und adressierte Transportqueue."""

import sqlalchemy as sa

from alembic import op

revision = "0312"
down_revision = "0311"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gateway", sa.Column("agent_worker_id", sa.String(96), nullable=True))
    op.add_column("gateway", sa.Column("agent_connection_token_hash", sa.String(64), nullable=True))
    op.add_column("sms_gateway_token", sa.Column("connection_token_hash", sa.String(64), nullable=True))
    for name, kind in (
        ("connection_id", sa.String(32)),
        ("worker_id", sa.String(96)),
        ("connected_at", sa.DateTime()),
        ("presence_expires_at", sa.DateTime()),
    ):
        op.add_column("sms_gateway_token", sa.Column(name, kind, nullable=True))
    op.create_table(
        "gateway_message",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gateway_kind", sa.String(20), nullable=False),
        sa.Column("gateway_id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(96), nullable=False),
        sa.Column("message", sa.JSON(), nullable=False),
        sa.Column("job_key", sa.String(128)),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.String(200)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_gateway_message_due", "gateway_message", ["worker_id", "state", "expires_at"])
    op.create_index("ix_gateway_message_result", "gateway_message", ["org_id", "gateway_kind", "gateway_id", "job_key"])
    op.create_index("ix_gateway_message_created", "gateway_message", ["created_at"])


def downgrade():
    op.drop_table("gateway_message")
    with op.batch_alter_table("sms_gateway_token") as batch:
        for name in ("connection_id", "worker_id", "connected_at", "presence_expires_at", "connection_token_hash"):
            batch.drop_column(name)
    op.drop_column("gateway", "agent_worker_id")
    op.drop_column("gateway", "agent_connection_token_hash")
