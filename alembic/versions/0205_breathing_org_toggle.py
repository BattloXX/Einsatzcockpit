"""Atemschutzueberwachung je Organisation schaltbar.

Revision ID: 0205
Revises: 0204
"""
import sqlalchemy as sa

from alembic import op

revision = "0205"
down_revision = "0204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.add_column(
            sa.Column(
                "atemschutz_ueberwachung_modul_aktiv",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("atemschutz_ueberwachung_modul_aktiv")
