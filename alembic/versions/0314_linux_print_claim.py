"""Atomarer Linux-Push und ausgehandelte Statusnachweise."""

import sqlalchemy as sa

from alembic import op

revision = "0314"
down_revision = "0313"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gateway", sa.Column("print_protocol", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("print_job", sa.Column("push_route_id", sa.String(32), nullable=True))


def downgrade():
    op.drop_column("print_job", "push_route_id")
    op.drop_column("gateway", "print_protocol")
