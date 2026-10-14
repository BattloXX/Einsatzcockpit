"""Ausschlussbegriffe fuer die Einsatzalarmierung je Organisation.

Revision ID: 0282
Revises: 0281
"""
from alembic import op
import sqlalchemy as sa

revision = "0282"
down_revision = "0281"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("org_settings") as batch_op:
        batch_op.add_column(sa.Column("alarm_ausschluss_begriffe", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("org_settings") as batch_op:
        batch_op.drop_column("alarm_ausschluss_begriffe")
