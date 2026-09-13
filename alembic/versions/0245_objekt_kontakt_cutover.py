"""Drop legacy object-contact master data after the central-contact cutover.

Revision ID: 0245
Revises: 0244
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0245"
down_revision = "0244"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    missing = connection.execute(
        sa.text("SELECT COUNT(*) FROM objekt_kontakt WHERE kontakt_id IS NULL")
    ).scalar_one()
    if missing:
        raise RuntimeError(
            "Kontakt-Cutover abgebrochen: "
            f"{missing} Objektzuordnung(en) haben keinen zentralen Kontakt."
        )
    ist_mysql = connection.dialect.name == "mysql"
    with op.batch_alter_table("objekt_kontakt") as batch:
        if ist_mysql:
            batch.drop_constraint("fk_objekt_kontakt_kontakt_id", type_="foreignkey")
        batch.alter_column("kontakt_id", existing_type=sa.BigInteger(), nullable=False)
        if ist_mysql:
            batch.create_foreign_key(
                "fk_objekt_kontakt_kontakt_id",
                "kontakt",
                ["kontakt_id"],
                ["id"],
                ondelete="RESTRICT",
            )
        batch.drop_column("name")
        batch.drop_column("telefone_json")
        batch.drop_column("email")
        batch.drop_column("benachrichtigung_mail")


def downgrade() -> None:
    ist_mysql = op.get_bind().dialect.name == "mysql"
    with op.batch_alter_table("objekt_kontakt") as batch:
        batch.add_column(sa.Column("name", sa.String(length=150), nullable=False, server_default=""))
        batch.add_column(sa.Column("telefone_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("email", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("benachrichtigung_mail", sa.Boolean(), nullable=False, server_default=sa.false()))
        if ist_mysql:
            batch.drop_constraint("fk_objekt_kontakt_kontakt_id", type_="foreignkey")
        batch.alter_column("kontakt_id", existing_type=sa.BigInteger(), nullable=True)
        if ist_mysql:
            batch.create_foreign_key(
                "fk_objekt_kontakt_kontakt_id",
                "kontakt",
                ["kontakt_id"],
                ["id"],
                ondelete="SET NULL",
            )
