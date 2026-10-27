"""Versionsnummer fuer die Alarmnachrichten eines Einsatzes."""

import sqlalchemy as sa

from alembic import op

revision = "0320"
down_revision = "0319"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alarm_ingest") as batch:
        batch.add_column(sa.Column("version_no", sa.Integer(), nullable=True))
        batch.create_index("ix_alarm_ingest_einsatz_version", ["einsatz_id", "version_no"])


def downgrade() -> None:
    with op.batch_alter_table("alarm_ingest") as batch:
        batch.drop_index("ix_alarm_ingest_einsatz_version")
        batch.drop_column("version_no")
