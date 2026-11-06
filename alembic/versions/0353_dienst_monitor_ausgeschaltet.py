"""Dienstueberwachung je Dienst abschalten; LIS bleibt zunaechst opt-in."""
import sqlalchemy as sa

from alembic import op

revision = "0353"
down_revision = "0352"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_settings", sa.Column(
        "dienst_monitor_ausgeschaltet", sa.String(255), nullable=True, server_default="alarm_lis",
    ))
    op.execute(sa.text("UPDATE org_settings SET dienst_monitor_ausgeschaltet = 'alarm_lis'"))


def downgrade() -> None:
    op.drop_column("org_settings", "dienst_monitor_ausgeschaltet")
