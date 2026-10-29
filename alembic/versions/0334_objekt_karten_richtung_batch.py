"""Bildrichtung und Laufzuordnung offener Kartenvorschlaege."""
import sqlalchemy as sa

from alembic import op

revision = "0334"
down_revision = "0333"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("objekt_karten_vorschlag") as batch:
        batch.add_column(sa.Column("bild_richtung_x", sa.Float(), nullable=True))
        batch.add_column(sa.Column("bild_richtung_y", sa.Float(), nullable=True))
        batch.add_column(sa.Column("batch_id", sa.String(64), nullable=True))
        batch.create_index("ix_objekt_karten_vorschlag_org_batch", ["org_id", "objekt_id", "batch_id"])


def downgrade() -> None:
    with op.batch_alter_table("objekt_karten_vorschlag") as batch:
        batch.drop_index("ix_objekt_karten_vorschlag_org_batch")
        for name in ("batch_id", "bild_richtung_y", "bild_richtung_x"):
            batch.drop_column(name)
