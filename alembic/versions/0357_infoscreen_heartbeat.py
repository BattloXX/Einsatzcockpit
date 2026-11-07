"""Technischer Displaystatus des Infoscreen-Agents."""
import sqlalchemy as sa
from alembic import op

revision = "0357"
down_revision = "0356"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("infoscreen_device", sa.Column("reported_config_version", sa.BigInteger(), nullable=True))
    op.add_column("infoscreen_display", sa.Column("reported_status", sa.JSON(), nullable=True))
    op.add_column("infoscreen_display", sa.Column("last_revision", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("infoscreen_device", "reported_config_version")
    op.drop_column("infoscreen_display", "last_revision")
    op.drop_column("infoscreen_display", "reported_status")
