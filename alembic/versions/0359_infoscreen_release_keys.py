"""Plattformweite, bestaetigte Release-Schluessel."""
import sqlalchemy as sa
from alembic import op

revision = "0359"
down_revision = "0358"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("infoscreen_release_key",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key_id", sa.String(100), nullable=False),
        sa.Column("pem", sa.Text, nullable=False),
        sa.Column("fingerprint", sa.String(71), nullable=False),
        sa.Column("aktiv", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("bestaetigt_von", sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bestaetigt_am", sa.DateTime, nullable=False),
        sa.Column("quelle", sa.String(16), nullable=False),
        sa.Column("erstellt_am", sa.DateTime, nullable=False),
        sa.UniqueConstraint("key_id", "fingerprint", name="uq_infoscreen_release_key_fingerprint"))


def downgrade() -> None:
    op.drop_table("infoscreen_release_key")
