"""Workeruebergreifende SMS-Verifizierung fuer Buergermeldungen."""

import sqlalchemy as sa

from alembic import op

revision = "0316"
down_revision = "0315"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "citizen_report_verification",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("major_incident_id", sa.Integer(), sa.ForeignKey("major_incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pin_hash", sa.String(64), nullable=False),
        sa.Column("phone", sa.String(120), nullable=False),
        sa.Column("form_data", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_citizen_report_verification_org_id", "citizen_report_verification", ["org_id"])
    op.create_index("ix_citizen_report_verification_expires_at", "citizen_report_verification", ["expires_at"])


def downgrade():
    op.drop_table("citizen_report_verification")
