"""Resend-Posteingang je Organisation.

Revision ID: 0271
Revises: 0270
"""
import sqlalchemy as sa
from alembic import op

revision = "0271"
down_revision = "0270"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_resend_mail_config", sa.Column("inbound_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("org_resend_mail_config", sa.Column("inbound_webhook_secret_enc", sa.Text(), nullable=True))
    op.add_column("org_resend_mail_config", sa.Column("inbound_retention_days", sa.Integer(), nullable=False, server_default="90"))
    op.create_table(
        "org_mail_eingang",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resend_email_id", sa.String(64), nullable=False), sa.Column("message_id", sa.String(500)),
        sa.Column("absender", sa.String(500), nullable=False), sa.Column("empfaenger", sa.Text(), nullable=False),
        sa.Column("cc", sa.Text()), sa.Column("betreff", sa.String(998), nullable=False),
        sa.Column("text_body", sa.Text()), sa.Column("html_body", sa.Text()), sa.Column("header_json", sa.Text()),
        sa.Column("spf", sa.String(20)), sa.Column("dkim", sa.String(20)), sa.Column("dmarc", sa.String(20)),
        sa.Column("anhang_json", sa.Text()), sa.Column("empfangen_at", sa.DateTime(), nullable=False),
        sa.Column("abgerufen_at", sa.DateTime()), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("fehler", sa.String(255)), sa.Column("gelesen_von", sa.BigInteger()), sa.Column("gelesen_at", sa.DateTime()),
        sa.UniqueConstraint("org_id", "resend_email_id", name="uq_org_mail_eingang_resend"),
    )
    op.create_index("ix_org_mail_eingang_org_empfangen", "org_mail_eingang", ["org_id", "empfangen_at"])


def downgrade() -> None:
    # MariaDB: Tabelle vor Spalten direkt droppen, keine Batch-Operationen.
    op.drop_table("org_mail_eingang")
    op.drop_column("org_resend_mail_config", "inbound_retention_days")
    op.drop_column("org_resend_mail_config", "inbound_webhook_secret_enc")
    op.drop_column("org_resend_mail_config", "inbound_enabled")
