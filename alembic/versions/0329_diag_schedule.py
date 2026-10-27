"""Globale Tagesdiagnose- und Benachrichtigungsclaims."""
import sqlalchemy as sa
from alembic import op

revision = "0329"
down_revision = "0328"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("diag_schedule_slot",
        sa.Column("slot", sa.String(10), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("diag_run.id", ondelete="SET NULL")))
    op.create_table("diag_notification",
        sa.Column("finding_key", sa.String(80), primary_key=True),
        sa.Column("last_sent_at", sa.DateTime()),
        sa.Column("count", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("diag_notification")
    op.drop_table("diag_schedule_slot")
