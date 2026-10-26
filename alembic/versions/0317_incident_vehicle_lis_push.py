"""Workeruebergreifendes Schutzfenster fuer LIS-Status-Pushes."""

import sqlalchemy as sa

from alembic import op

revision = "0317"
down_revision = "0316"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("incident_vehicle", sa.Column("lis_pushed_status", sa.String(16), nullable=True))
    op.add_column("incident_vehicle", sa.Column("lis_pushed_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("incident_vehicle", "lis_pushed_at")
    op.drop_column("incident_vehicle", "lis_pushed_status")
