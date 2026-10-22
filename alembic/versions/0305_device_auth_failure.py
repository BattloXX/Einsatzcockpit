"""Fehlversuche mit ungueltigen Geraetezugaengen und Geraetemerkmale (SEC-01)."""

import sqlalchemy as sa

from alembic import op

revision = "0305"
down_revision = "0304"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("device_token", sa.Column("last_failed_at", sa.DateTime, nullable=True))
    op.add_column("device_token", sa.Column("fail_count", sa.Integer, nullable=False, server_default="0"))
    op.add_column("device_token", sa.Column("platform", sa.String(20), nullable=True))
    op.add_column("device_token", sa.Column("os_version", sa.String(40), nullable=True))
    op.add_column("device_token", sa.Column("device_model", sa.String(80), nullable=True))
    op.create_table(
        "device_auth_failure",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("device_token_id", sa.BigInteger, sa.ForeignKey("device_token.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("token_hash_prefix", sa.String(12), nullable=False, server_default=""),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("first_at", sa.DateTime, nullable=False),
        sa.Column("last_at", sa.DateTime, nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("app_version", sa.String(30), nullable=True),
        sa.Column("platform", sa.String(20), nullable=True),
        sa.UniqueConstraint("token_hash_prefix", "reason", "channel", name="uq_device_auth_failure"),
    )
    op.create_index("ix_device_auth_failure_device_token_id", "device_auth_failure", ["device_token_id"])


def downgrade() -> None:
    op.drop_index("ix_device_auth_failure_device_token_id", table_name="device_auth_failure")
    op.drop_table("device_auth_failure")
    with op.batch_alter_table("device_token") as batch:
        for spalte in ("device_model", "os_version", "platform", "fail_count", "last_failed_at"):
            batch.drop_column(spalte)
