"""Abzug beendeter Einheiten von Einsatzstellen.

Revision ID: 0280
Revises: 0279
"""

import sqlalchemy as sa

from alembic import op

revision = "0280"
down_revision = "0279"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("einheit_site_dispatch", sa.Column("abgezogen_at", sa.DateTime(), nullable=True))
    op.add_column("einheit_site_dispatch", sa.Column("abgezogen_by", sa.BigInteger(), nullable=True))
    op.add_column("einheit_site_dispatch", sa.Column("abgezogen_author", sa.String(length=120), nullable=True))
    with op.batch_alter_table("einheit_site_dispatch") as batch_op:
        batch_op.create_foreign_key(
            "fk_einheit_site_dispatch_abgezogen_by_user",
            "user",
            ["abgezogen_by"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("einheit_site_dispatch") as batch_op:
        batch_op.drop_constraint("fk_einheit_site_dispatch_abgezogen_by_user", type_="foreignkey")
    op.drop_column("einheit_site_dispatch", "abgezogen_author")
    op.drop_column("einheit_site_dispatch", "abgezogen_by")
    op.drop_column("einheit_site_dispatch", "abgezogen_at")
