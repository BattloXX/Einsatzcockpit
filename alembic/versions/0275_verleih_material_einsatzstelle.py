"""Material an Einsatzstellen im Geraeteverleih.

Revision ID: 0275
Revises: 0274
"""
import sqlalchemy as sa

from alembic import op

revision = "0275"
down_revision = "0274"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("verleih_ausleihe") as batch:
            batch.add_column(sa.Column("quelle", sa.String(12), nullable=False, server_default="verleih"))
            batch.add_column(sa.Column("einheit_id", sa.Integer(), nullable=True))
            batch.add_column(sa.Column("created_by_name", sa.String(160), nullable=True))
            batch.create_index("ix_verleih_ausleihe_einheit_id", ["einheit_id"])
            batch.create_foreign_key(
                "fk_verleih_ausleihe_einheit", "lage_einheit", ["einheit_id"], ["id"], ondelete="SET NULL"
            )
        with op.batch_alter_table("verleih_foto") as batch:
            batch.add_column(sa.Column("author_name", sa.String(160), nullable=True))
        return
    op.add_column("verleih_ausleihe", sa.Column("quelle", sa.String(12), nullable=False, server_default="verleih"))
    op.add_column("verleih_ausleihe", sa.Column("einheit_id", sa.Integer(), nullable=True))
    op.add_column("verleih_ausleihe", sa.Column("created_by_name", sa.String(160), nullable=True))
    op.create_index("ix_verleih_ausleihe_einheit_id", "verleih_ausleihe", ["einheit_id"])
    op.create_foreign_key(
        "fk_verleih_ausleihe_einheit", "verleih_ausleihe", "lage_einheit", ["einheit_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("verleih_foto", sa.Column("author_name", sa.String(160), nullable=True))


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("verleih_foto") as batch:
            batch.drop_column("author_name")
        with op.batch_alter_table("verleih_ausleihe") as batch:
            batch.drop_constraint("fk_verleih_ausleihe_einheit", type_="foreignkey")
            batch.drop_index("ix_verleih_ausleihe_einheit_id")
            batch.drop_column("created_by_name")
            batch.drop_column("einheit_id")
            batch.drop_column("quelle")
        return
    op.drop_column("verleih_foto", "author_name")
    op.drop_constraint("fk_verleih_ausleihe_einheit", "verleih_ausleihe", type_="foreignkey")
    op.drop_index("ix_verleih_ausleihe_einheit_id", table_name="verleih_ausleihe")
    op.drop_column("verleih_ausleihe", "created_by_name")
    op.drop_column("verleih_ausleihe", "einheit_id")
    op.drop_column("verleih_ausleihe", "quelle")
