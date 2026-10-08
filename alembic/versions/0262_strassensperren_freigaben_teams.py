"""Straßensperren: Freigabelinks und Teams-Grundlagen.

Revision ID: 0262
Revises: 0261
"""

import sqlalchemy as sa

from alembic import op

revision = "0262"
down_revision = "0261"
branch_labels = None
depends_on = None


def _tenant_org_id() -> sa.Column:
    return sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), index=True)


def upgrade() -> None:
    op.add_column("road_closure", sa.Column("reason", sa.String(300), nullable=True))
    op.add_column("road_closure", sa.Column("teams_melden", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "road_closure_access_token",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("art", sa.String(12), nullable=False),
        sa.Column("road_closure_id", sa.BigInteger(), sa.ForeignKey("road_closure.id", ondelete="CASCADE")),
        sa.Column("label", sa.String(120)),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_enc", sa.Text()),
        sa.Column("berechtigungen_json", sa.Text()),
        sa.Column("expires_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("last_used_at", sa.DateTime()),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_road_closure_access_token_road_closure_id", "road_closure_access_token", ["road_closure_id"])
    op.create_table(
        "road_closure_teams_config",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("webhook_url_enc", sa.Text()),
        sa.Column("auto_neu", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_aenderung", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_aufhebung", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("standard_melden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("include_map", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime()),
        sa.Column("updated_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.UniqueConstraint("org_id"),
    )
    op.create_table(
        "road_closure_notification",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        _tenant_org_id(),
        sa.Column(
            "road_closure_id", sa.BigInteger(), sa.ForeignKey("road_closure.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("ereignis", sa.String(30), nullable=False),
        sa.Column("dedup_key", sa.String(120), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime()),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("last_error", sa.String(500)),
        sa.Column("payload_fingerprint", sa.String(64)),
        sa.Column("source", sa.String(10), nullable=False, server_default="ui"),
        sa.Column("triggered_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime()),
        sa.UniqueConstraint("road_closure_id", "dedup_key"),
    )
    op.create_index("ix_road_closure_notification_road_closure_id", "road_closure_notification", ["road_closure_id"])
    op.create_index(
        "ix_road_closure_notification_due", "road_closure_notification", ["org_id", "status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_road_closure_notification_due", table_name="road_closure_notification")
    op.drop_index("ix_road_closure_notification_road_closure_id", table_name="road_closure_notification")
    op.drop_table("road_closure_notification")
    op.drop_table("road_closure_teams_config")
    op.drop_index("ix_road_closure_access_token_road_closure_id", table_name="road_closure_access_token")
    op.drop_table("road_closure_access_token")
    op.drop_column("road_closure", "teams_melden")
    op.drop_column("road_closure", "reason")
