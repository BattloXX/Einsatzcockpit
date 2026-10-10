"""GSL: automatische Auftrags-SMS.

Revision ID: 0270
Revises: 0269
"""

import sqlalchemy as sa

from alembic import op

revision = "0270"
down_revision = "0269"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_settings", sa.Column("gk_auto_sms_auftrag", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("org_settings", sa.Column("gk_auftrag_nachricht", sa.Text(), nullable=True))


def downgrade() -> None:
    # Keine abhängigen FKs/Indizes: MariaDB kann die Spalten direkt entfernen.
    op.drop_column("org_settings", "gk_auftrag_nachricht")
    op.drop_column("org_settings", "gk_auto_sms_auftrag")
