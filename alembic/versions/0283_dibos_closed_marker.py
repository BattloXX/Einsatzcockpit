"""DIBOS-Abschlussmarker fuer automatische Wiederabschluesse.

Revision ID: 0283
Revises: 0282
"""
from alembic import op
import sqlalchemy as sa

revision = "0283"
down_revision = "0282"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("incident") as batch_op:
        batch_op.add_column(sa.Column("dibos_closed_marker", sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table("incident") as batch_op:
        batch_op.drop_column("dibos_closed_marker")
