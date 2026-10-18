"""Einsatzbezogene Kontaktfreigaben.

Revision ID: 0292
Revises: 0291
"""

import sqlalchemy as sa

from alembic import op

revision = "0292"
down_revision = "0291"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bestehende Objektkontakte waren bereits fuer Einsaetze sichtbar.
    op.add_column(
        "objekt_kontakt", sa.Column("einsatz_sichtbar", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.create_table(
        "einsatz_kontakt",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kontakt_id", sa.BigInteger(), sa.ForeignKey("kontakt.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hinweis", sa.String(500), nullable=True),
        sa.UniqueConstraint("incident_id", "kontakt_id", name="uq_einsatz_kontakt"),
    )
    op.create_index("ix_einsatz_kontakt_org_id", "einsatz_kontakt", ["org_id"])
    op.create_index("ix_einsatz_kontakt_incident_id", "einsatz_kontakt", ["incident_id"])


def downgrade() -> None:
    op.drop_table("einsatz_kontakt")
    with op.batch_alter_table("objekt_kontakt") as batch:
        batch.drop_column("einsatz_sichtbar")
