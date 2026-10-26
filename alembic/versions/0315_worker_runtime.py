"""Ablaufende Worker-Heartbeats und sichere Laufdiagnose."""

import sqlalchemy as sa

from alembic import op

revision = "0315"
down_revision = "0314"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "worker_runtime",
        sa.Column("worker_id", sa.String(96), primary_key=True),
        sa.Column("hostname", sa.String(64), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("is_leader", sa.Boolean(), nullable=False),
        sa.Column("leader_since", sa.DateTime()),
        sa.Column("leader_error", sa.String(40)),
        sa.Column("redis", sa.JSON(), nullable=False),
        sa.Column("loops", sa.JSON(), nullable=False),
    )
    op.create_index("ix_worker_runtime_expiry", "worker_runtime", ["expires_at"])


def downgrade():
    op.drop_table("worker_runtime")
