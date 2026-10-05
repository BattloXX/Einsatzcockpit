"""Alarm-Outbox und Lease fuer Einsatzinfo-SMS.

Revision ID: 0256
Revises: 0255
"""
import sqlalchemy as sa
from alembic import op

revision = "0256"
down_revision = "0255"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_alarm_job",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incident.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("incident_id", "channel", name="uq_incident_alarm_job_incident_channel"),
    )
    op.create_index("ix_incident_alarm_job_org_id", "incident_alarm_job", ["org_id"])
    op.create_index("ix_incident_alarm_job_incident_id", "incident_alarm_job", ["incident_id"])
    op.create_index("ix_incident_alarm_job_due", "incident_alarm_job", ["org_id", "status", "next_attempt_at"])
    with op.batch_alter_table("sms_log_recipient") as batch:
        batch.add_column(sa.Column("lease_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sms_log_recipient") as batch:
        batch.drop_column("lease_until")
    op.drop_index("ix_incident_alarm_job_due", table_name="incident_alarm_job")
    op.drop_index("ix_incident_alarm_job_incident_id", table_name="incident_alarm_job")
    op.drop_index("ix_incident_alarm_job_org_id", table_name="incident_alarm_job")
    op.drop_table("incident_alarm_job")
