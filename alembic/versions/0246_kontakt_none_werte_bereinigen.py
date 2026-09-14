"""Normalize legacy textual NULL values in central contacts.

Revision ID: 0246
Revises: 0245
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0246"
down_revision = "0245"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    for feld in (
        "vorname",
        "nachname",
        "funktion",
        "organisation",
        "email",
        "erreichbarkeit",
        "notizen",
        "bild_pfad",
    ):
        connection.execute(
            sa.text(f"UPDATE kontakt SET {feld} = NULL WHERE LOWER(TRIM({feld})) IN ('none', 'null', '')")
        )
    connection.execute(
        sa.text("UPDATE kontakt_telefon SET label = NULL WHERE LOWER(TRIM(label)) IN ('none', 'null', '')")
    )

    if connection.dialect.name == "mysql":
        name_expr = "NULLIF(TRIM(CONCAT_WS(' ', vorname, nachname)), '')"
    else:
        name_expr = "NULLIF(TRIM(COALESCE(vorname, '') || ' ' || COALESCE(nachname, '')), '')"
    connection.execute(
        sa.text(
            "UPDATE kontakt "
            f"SET anzeigename = COALESCE({name_expr}, 'Unbenannt') "
            "WHERE LOWER(TRIM(anzeigename)) IN ('none', 'null', '')"
        )
    )


def downgrade() -> None:
    # Die Bereinigung entfernt fehlerhafte Platzhalterwerte und ist absichtlich irreversibel.
    pass
