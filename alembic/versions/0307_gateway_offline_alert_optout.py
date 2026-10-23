"""Ausfallalarmierung pro Druck-Gateway abschaltbar, Bestand bleibt aktiv."""
import sqlalchemy as sa

from alembic import op

revision = "0307"
down_revision = "0306"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gateway", sa.Column("offline_alert_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("gateway") as batch:
        batch.drop_column("offline_alert_enabled")
