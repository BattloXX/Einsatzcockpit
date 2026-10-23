"""Befristete, workeruebergreifende Windows-Druckagent-Verbindung."""
import sqlalchemy as sa

from alembic import op

revision = "0306"
down_revision = "0305"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, kind in (("agent_connection_id", sa.String(32)),
                       ("agent_connected_at", sa.DateTime()),
                       ("agent_heartbeat_at", sa.DateTime()),
                       ("agent_presence_expires_at", sa.DateTime())):
        op.add_column("gateway", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("gateway") as batch:
        for name in ("agent_presence_expires_at", "agent_heartbeat_at", "agent_connected_at", "agent_connection_id"):
            batch.drop_column(name)
