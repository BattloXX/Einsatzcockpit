"""incident.reason auf TEXT erweitern (war String(500), Modell nutzt Text)

Revision ID: 0198
Revises: 0197
Create Date: 2026-08-12 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0198"
down_revision = "0197"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ursprünglich als String(500) angelegt (0001_initial.py), das Modell
    # (app/models/incident.py) nutzt seit jeher Text - bei langen Freitexten
    # aus dem Leitstellen-Import (Format B) schlug INSERT mit
    # "Data too long for column 'reason'" fehl.
    op.alter_column(
        "incident", "reason",
        existing_type=sa.String(500),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "incident", "reason",
        existing_type=sa.Text(),
        type_=sa.String(500),
        existing_nullable=True,
    )
