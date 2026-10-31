"""Kurzcode fuer den SMS-Einsatzinfo-Link (/a/<code>)."""

import sqlalchemy as sa

from alembic import op

revision = "0338"
down_revision = "0337"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incident", sa.Column("alarm_kurzcode", sa.String(16), nullable=True))
    op.add_column("alarm_token", sa.Column("kurzcode_hash", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_alarm_token_kurzcode_hash", "alarm_token", ["kurzcode_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_alarm_token_kurzcode_hash", "alarm_token", type_="unique")
    op.drop_column("alarm_token", "kurzcode_hash")
    op.drop_column("incident", "alarm_kurzcode")
