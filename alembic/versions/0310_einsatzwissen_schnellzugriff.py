"""Persönliche Favoriten und Nutzungshäufigkeit für Einsatzwissen."""

import sqlalchemy as sa

from alembic import op

revision = "0310"
down_revision = "0309"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("katplan_nutzung", sa.Column("favorit", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("katplan_nutzung", sa.Column("aufrufe", sa.Integer(), nullable=False, server_default="0"))
    op.execute("UPDATE katplan_nutzung SET aufrufe = 1")


def downgrade() -> None:
    with op.batch_alter_table("katplan_nutzung") as batch:
        batch.drop_column("aufrufe")
        batch.drop_column("favorit")
