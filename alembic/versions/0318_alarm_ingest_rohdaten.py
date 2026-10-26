"""Rohdaten dauerhaft speichern und Verarbeitung getrennt nachverfolgen."""

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMBLOB

from alembic import op

revision = "0318"
down_revision = "0317"
branch_labels = None
depends_on = None


def _columns():
    return (
        sa.Column("raw_bytes", sa.LargeBinary().with_variant(MEDIUMBLOB(), "mysql", "mariadb"), nullable=True),
        sa.Column("source", sa.String(20), nullable=True, server_default="wut"),
        sa.Column("received_at_gateway", sa.DateTime(), nullable=True),
        sa.Column("sequence", sa.BigInteger(), nullable=True),
        sa.Column("process_status", sa.String(20), nullable=True, server_default="applied"),
        sa.Column("process_error", sa.Text(), nullable=True),
        sa.Column("process_attempts", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("parser_version", sa.String(20), nullable=True),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("leitstellennummer", sa.String(40), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    constraints = inspector.get_unique_constraints("alarm_ingest")
    indexes = inspector.get_indexes("alarm_ingest")
    with op.batch_alter_table(
        "alarm_ingest", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}
    ) as batch:
        for column in _columns():
            batch.add_column(column)
        # MariaDB meldet denselben UNIQUE sowohl als Constraint als auch als Index.
        constraint_names = set()
        for constraint in constraints:
            if constraint["column_names"] == ["raw_hash"]:
                name = constraint["name"] or "uq_alarm_ingest_raw_hash"
                batch.drop_constraint(name, type_="unique")
                constraint_names.add(name)
        for index in indexes:
            if (index["unique"] and index["column_names"] == ["raw_hash"]
                    and index["name"] not in constraint_names
                    and not index.get("duplicates_constraint")):
                batch.drop_index(index["name"])
        batch.create_unique_constraint("uq_alarm_ingest_gateway_sequence", ["gateway_id", "sequence"])
        batch.create_index("ix_alarm_ingest_leitstellennummer", ["leitstellennummer"])
        batch.create_index("ix_alarm_ingest_org_nummer_hash", ["org_id", "leitstellennummer", "content_hash"])
    op.execute(sa.text("UPDATE alarm_ingest SET process_status='applied', content_hash=raw_hash"))


def downgrade() -> None:
    # Bei inzwischen mehrfach vorhandenem raw_hash scheitert dies ohne Datenverlust.
    with op.batch_alter_table("alarm_ingest") as batch:
        batch.drop_index("ix_alarm_ingest_org_nummer_hash")
        batch.drop_index("ix_alarm_ingest_leitstellennummer")
        batch.drop_constraint("uq_alarm_ingest_gateway_sequence", type_="unique")
        batch.create_unique_constraint("uq_alarm_ingest_raw_hash", ["raw_hash"])
        for column in reversed(_columns()):
            batch.drop_column(column.name)
