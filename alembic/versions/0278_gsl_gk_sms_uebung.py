"""Eigener Uebungs-Schalter fuer GK-SMS.

Revision ID: 0278
Revises: 0277
"""

import sqlalchemy as sa

from alembic import op

revision = "0278"
down_revision = "0277"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_settings",
        sa.Column("gk_sms_uebung", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        sa.text(
            "UPDATE org_settings SET gk_sms_uebung = einsatzinfo_sms_send_exercise "
            "WHERE einsatzinfo_sms_send_exercise IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("org_settings", "gk_sms_uebung")
