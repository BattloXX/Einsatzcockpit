"""SMS-Empfaengerdetails und Protokoll-Aufbewahrung.

Revision ID: 0194
Revises: 0193
"""
import sqlalchemy as sa

from alembic import op

revision = "0194"
down_revision = "0193"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "org_sms_config",
        sa.Column("log_retention_days", sa.Integer(), nullable=False, server_default="90"),
    )
    op.add_column(
        "org_sms_config",
        sa.Column("log_retention_max_count", sa.Integer(), nullable=False, server_default="500"),
    )
    op.create_table(
        "sms_log_recipient",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sms_log_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=True),
        sa.Column("phone_number", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sms_log_id"], ["sms_log.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sms_log_recipient_sms_log_id", "sms_log_recipient", ["sms_log_id"], unique=False
    )


def downgrade():
    op.drop_index("ix_sms_log_recipient_sms_log_id", table_name="sms_log_recipient")
    op.drop_table("sms_log_recipient")
    op.drop_column("org_sms_config", "log_retention_max_count")
    op.drop_column("org_sms_config", "log_retention_days")
