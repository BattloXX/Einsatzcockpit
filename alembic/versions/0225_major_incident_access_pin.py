"""PIN-Zugang für Großschadenslagen.

Revision ID: 0225
Revises: 0224
"""
import sqlalchemy as sa

from alembic import op

revision = "0225"
down_revision = "0224"
branch_labels = None
depends_on = None


def _hat_spalte(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(spalte["name"] == name for spalte in inspector.get_columns("major_incident"))


def upgrade() -> None:
    if not _hat_spalte("access_pin_hash"):
        with op.batch_alter_table("major_incident") as batch_op:
            batch_op.add_column(sa.Column("access_pin_hash", sa.String(120), nullable=True))


def downgrade() -> None:
    if _hat_spalte("access_pin_hash"):
        with op.batch_alter_table("major_incident") as batch_op:
            batch_op.drop_column("access_pin_hash")
