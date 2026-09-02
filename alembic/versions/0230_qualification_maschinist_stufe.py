"""Maschinisten-Stufe am Qualifikationskatalog.

Revision ID: 0230
Revises: 0229
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0230"
down_revision = "0229"
branch_labels = None
depends_on = None


def _spalten(bind) -> set[str]:
    return {spalte["name"] for spalte in sa.inspect(bind).get_columns("qualification")}


def upgrade() -> None:
    bind = op.get_bind()
    if "maschinist_stufe" not in _spalten(bind):
        with op.batch_alter_table("qualification") as batch:
            batch.add_column(sa.Column("maschinist_stufe", sa.SmallInteger(), nullable=True))

    qualification = sa.table(
        "qualification",
        sa.column("code", sa.String()),
        sa.column("label", sa.String()),
        sa.column("maschinist_stufe", sa.SmallInteger()),
    )
    for stufe in range(1, 5):
        code = f"M{stufe}"
        vorhanden = bind.execute(
            sa.select(qualification.c.code).where(qualification.c.code == code)
        ).first()
        if vorhanden:
            bind.execute(
                qualification.update()
                .where(qualification.c.code == code)
                .values(maschinist_stufe=stufe)
            )
        else:
            bind.execute(qualification.insert().values(
                code=code, label=f"Maschinist Stufe {stufe}", maschinist_stufe=stufe,
            ))


def downgrade() -> None:
    bind = op.get_bind()
    if "maschinist_stufe" not in _spalten(bind):
        return
    with op.batch_alter_table("qualification") as batch:
        batch.drop_column("maschinist_stufe")
