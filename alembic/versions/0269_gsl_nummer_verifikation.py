"""GSL: bestätigte Mobilnummer des Gruppenkommandanten.

Revision ID: 0269
Revises: 0268
"""
import sqlalchemy as sa

from alembic import op

revision = "0269"
down_revision = "0268"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lage_einheit_leader", sa.Column("phone_verifiziert_at", sa.DateTime(), nullable=True))
    op.create_table(
        "lage_einheit_nummer_verifikation",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("lage_id", sa.Integer(), nullable=False),
        sa.Column("einheit_id", sa.Integer(), sa.ForeignKey("lage_einheit.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "leader_id", sa.Integer(), sa.ForeignKey("lage_einheit_leader.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("phone_version", sa.Integer(), nullable=False),
        sa.Column("phone_e164_neu", sa.String(20), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("gueltig_bis", sa.DateTime(), nullable=False),
        sa.Column("versuche", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gesperrt_bis", sa.DateTime()), sa.Column("zugang_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("verbraucht_at", sa.DateTime()),
    )
    op.create_index("ix_lenv_einheit", "lage_einheit_nummer_verifikation", ["einheit_id"])
    op.create_index("ix_lenv_leader", "lage_einheit_nummer_verifikation", ["leader_id"])


def downgrade() -> None:
    # Die Tabelle samt FKs und Indizes in einem Schritt entfernen: MariaDB erlaubt kein
    # Droppen eines FK-stützenden Index vor der Tabelle (Fehler 1553).
    op.drop_table("lage_einheit_nummer_verifikation")
    op.drop_column("lage_einheit_leader", "phone_verifiziert_at")
