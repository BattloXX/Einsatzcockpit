"""api_key.org_id wird Pflicht (Backfill auf Home-Org / Wolfurt)

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-25 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Backfill: NULL-org_id-Keys auf die Home-Org (Fallback: erste fire_dept)
    op.execute(
        """
        UPDATE api_key
        SET org_id = (
            SELECT id FROM fire_dept
            ORDER BY is_home_org DESC, id ASC
            LIMIT 1
        )
        WHERE org_id IS NULL
        """
    )

    # 2) MariaDB erlaubt kein ALTER COLUMN auf FK-Spalten (Error 1832).
    #    → FK temporär entfernen.
    op.drop_constraint("fk_api_key_org_id", "api_key", type_="foreignkey")

    # 3) NOT NULL setzen. existing_type MUSS BigInteger sein (so wurde die
    #    Spalte in 0002 angelegt) — sonst würde MariaDB den Typ implizit
    #    auf INT verkleinern und der Re-Create des FK schlägt fehl.
    op.alter_column(
        "api_key", "org_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    # 4) FK neu anlegen. ondelete="RESTRICT" statt "SET NULL", weil die
    #    Spalte jetzt NOT NULL ist — SET NULL würde zur Laufzeit krachen.
    op.create_foreign_key(
        "fk_api_key_org_id", "api_key", "fire_dept",
        ["org_id"], ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_api_key_org_id", "api_key", type_="foreignkey")
    op.alter_column(
        "api_key", "org_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_api_key_org_id", "api_key", "fire_dept",
        ["org_id"], ["id"],
        ondelete="SET NULL",
    )
