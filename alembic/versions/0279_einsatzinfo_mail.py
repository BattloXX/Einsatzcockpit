"""Datenmodell fuer Einsatzinfo-Mail.

Revision ID: 0279
Revises: 0278
"""

import sqlalchemy as sa

from alembic import op

revision = "0279"
down_revision = "0278"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_settings",
        sa.Column("einsatzinfo_mail_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "org_settings",
        sa.Column("einsatzinfo_mail_send_exercise", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "org_settings",
        sa.Column("einsatzinfo_mail_subject_template", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "org_settings",
        sa.Column("einsatzinfo_mail_template", sa.Text(), nullable=True),
    )
    op.add_column(
        "alarm_type",
        sa.Column("einsatzinfo_mail_subject_template", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "alarm_type",
        sa.Column("einsatzinfo_mail_template", sa.Text(), nullable=True),
    )

    op.create_table(
        "mail_einsatzinfo_recipient",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.Column("alarm_type_id", sa.BigInteger(), nullable=True),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("member_id", sa.BigInteger(), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["alarm_type_id"], ["alarm_type.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["sms_group.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mail_einsatzinfo_recipient_org_id", "mail_einsatzinfo_recipient", ["org_id"])
    op.create_index(
        "ix_mail_einsatzinfo_recipient_alarm_type_id",
        "mail_einsatzinfo_recipient",
        ["alarm_type_id"],
    )

    op.create_table(
        "mail_einsatzinfo_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("alarm_type_code", sa.String(length=10), nullable=True),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incident.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mail_einsatzinfo_log_org_id", "mail_einsatzinfo_log", ["org_id"])
    op.create_index("ix_mail_einsatzinfo_log_sent_at", "mail_einsatzinfo_log", ["sent_at"])
    op.create_index("ix_mail_einsatzinfo_log_incident_id", "mail_einsatzinfo_log", ["incident_id"])

    op.create_table(
        "mail_einsatzinfo_log_recipient",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("log_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["log_id"], ["mail_einsatzinfo_log.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mail_einsatzinfo_log_recipient_log_id",
        "mail_einsatzinfo_log_recipient",
        ["log_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mail_einsatzinfo_log_recipient_log_id", table_name="mail_einsatzinfo_log_recipient")
    op.drop_table("mail_einsatzinfo_log_recipient")
    op.drop_index("ix_mail_einsatzinfo_log_incident_id", table_name="mail_einsatzinfo_log")
    op.drop_index("ix_mail_einsatzinfo_log_sent_at", table_name="mail_einsatzinfo_log")
    op.drop_index("ix_mail_einsatzinfo_log_org_id", table_name="mail_einsatzinfo_log")
    op.drop_table("mail_einsatzinfo_log")
    op.drop_index("ix_mail_einsatzinfo_recipient_alarm_type_id", table_name="mail_einsatzinfo_recipient")
    op.drop_index("ix_mail_einsatzinfo_recipient_org_id", table_name="mail_einsatzinfo_recipient")
    op.drop_table("mail_einsatzinfo_recipient")
    op.drop_column("alarm_type", "einsatzinfo_mail_template")
    op.drop_column("alarm_type", "einsatzinfo_mail_subject_template")
    op.drop_column("org_settings", "einsatzinfo_mail_template")
    op.drop_column("org_settings", "einsatzinfo_mail_subject_template")
    op.drop_column("org_settings", "einsatzinfo_mail_send_exercise")
    op.drop_column("org_settings", "einsatzinfo_mail_enabled")
