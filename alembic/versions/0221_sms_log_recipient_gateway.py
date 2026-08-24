"""SMS-Empfaenger um Provider- und Gateway-Snapshot erweitern.

Revision ID: 0221
Revises: 0220
"""

import sqlalchemy as sa

from alembic import op

revision = "0221"
down_revision = "0220"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sms_log_recipient", sa.Column("provider", sa.String(20), nullable=True))
    op.add_column("sms_log_recipient", sa.Column("gateway_label", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("sms_log_recipient", "gateway_label")
    op.drop_column("sms_log_recipient", "provider")
