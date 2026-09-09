"""Assign rescued people to a concrete board column.

Revision ID: 0237
Revises: 0236
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0237"
down_revision = "0236"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("rescued_person")}
    if "column_id" in columns:
        return
    op.add_column("rescued_person", sa.Column("column_id", sa.BigInteger(), nullable=True))
    # Existing boards historically had one effective rescued lane.  Prefer its
    # display order, then id, so every existing person has a deterministic home.
    op.execute(sa.text("""
        UPDATE rescued_person AS person
        SET column_id = (
            SELECT col.id FROM incident_column AS col
            WHERE col.incident_id = person.incident_id AND col.column_kind = 'rescued'
            ORDER BY col.display_order, col.id
            LIMIT 1
        )
        WHERE person.column_id IS NULL
    """))
    # SQLite needs a table rebuild for a foreign key and NOT NULL constraint;
    # batch_alter_table emits ordinary ALTER statements on MariaDB/MySQL.
    with op.batch_alter_table("rescued_person") as batch_op:
        batch_op.create_foreign_key(
            "fk_rescued_person_column_id", "incident_column", ["column_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_index("ix_rescued_person_column_id", ["column_id"])
        batch_op.alter_column("column_id", existing_type=sa.BigInteger(), nullable=False)


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("rescued_person")}
    if "column_id" not in columns:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("rescued_person") as batch_op:
            batch_op.drop_constraint("fk_rescued_person_column_id", type_="foreignkey")
            batch_op.drop_index("ix_rescued_person_column_id")
            batch_op.drop_column("column_id")
        return
    op.drop_constraint("fk_rescued_person_column_id", "rescued_person", type_="foreignkey")
    op.drop_index("ix_rescued_person_column_id", table_name="rescued_person")
    op.drop_column("rescued_person", "column_id")
