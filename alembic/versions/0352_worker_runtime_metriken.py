"""Prozessmetriken im Worker-Heartbeat."""
import sqlalchemy as sa

from alembic import op

revision = "0352"
down_revision = "0351"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worker_runtime", sa.Column("metriken", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("worker_runtime", "metriken")
