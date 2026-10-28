"""Quellhash freigegebener Marker fuer persistente Veraltet-Pruefung."""
import sqlalchemy as sa

from alembic import op

revision = "0333"
down_revision = "0332"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("objekt_karten_objekt", sa.Column("quelle_dokument_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("objekt_karten_objekt", "quelle_dokument_hash")
