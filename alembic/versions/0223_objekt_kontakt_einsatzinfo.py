"""Einsatzinfo an Objektkontakte.

Revision ID: 0223
Revises: 0222
"""
from alembic import op
import sqlalchemy as sa

revision = "0223"
down_revision = "0222"
branch_labels = None
depends_on = None


def upgrade():
    ist_sqlite = op.get_bind().dialect.name == "sqlite"
    for name in ("kontakt_info_enabled", "kontakt_info_uebung"):
        op.add_column(
            "objekt",
            sa.Column(name, sa.Boolean(), nullable=False, server_default="0"),
        )
        if not ist_sqlite:
            op.alter_column("objekt", name, server_default=None)
    op.add_column("objekt", sa.Column("kontakt_info_stichworte", sa.String(200), nullable=True))
    op.add_column("objekt", sa.Column("kontakt_info_betreff", sa.String(200), nullable=True))
    op.add_column("objekt", sa.Column("kontakt_info_template", sa.Text(), nullable=True))

    for name in ("benachrichtigung_mail", "benachrichtigung_sms"):
        op.add_column(
            "objekt_kontakt",
            sa.Column(name, sa.Boolean(), nullable=False, server_default="0"),
        )
        if not ist_sqlite:
            op.alter_column("objekt_kontakt", name, server_default=None)
    op.add_column(
        "objekt_kontakt",
        sa.Column("benachrichtigung_telefon", sa.String(30), nullable=True),
    )
    op.add_column(
        "org_settings",
        sa.Column("objekt_kontakt_info_betreff", sa.String(200), nullable=True),
    )
    op.add_column(
        "org_settings",
        sa.Column("objekt_kontakt_info_template", sa.Text(), nullable=True),
    )

    op.create_table(
        "objekt_kontakt_benachrichtigung",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("objekt_id", sa.BigInteger(), nullable=False),
        sa.Column("objekt_kontakt_id", sa.BigInteger(), nullable=True),
        sa.Column("kanal", sa.String(10), nullable=False),
        sa.Column("kontakt_name", sa.String(150), nullable=True),
        sa.Column("empfaenger", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("fehlertext", sa.String(500), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("gesendet_am", sa.DateTime(), nullable=False),
        sa.Column("ausgeloest_von_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incident.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["objekt_id"], ["objekt.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["objekt_kontakt_id"], ["objekt_kontakt.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["ausgeloest_von_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "incident_id", "objekt_kontakt_id", "kanal",
            name="uq_objekt_kontakt_benachrichtigung",
        ),
    )
    op.create_index(
        "ix_okb_org_incident", "objekt_kontakt_benachrichtigung", ["org_id", "incident_id"]
    )
    op.create_index(
        "ix_okb_org_objekt", "objekt_kontakt_benachrichtigung", ["org_id", "objekt_id"]
    )


def downgrade():
    op.drop_index("ix_okb_org_objekt", table_name="objekt_kontakt_benachrichtigung")
    op.drop_index("ix_okb_org_incident", table_name="objekt_kontakt_benachrichtigung")
    op.drop_table("objekt_kontakt_benachrichtigung")
    op.drop_column("org_settings", "objekt_kontakt_info_template")
    op.drop_column("org_settings", "objekt_kontakt_info_betreff")
    op.drop_column("objekt_kontakt", "benachrichtigung_telefon")
    op.drop_column("objekt_kontakt", "benachrichtigung_sms")
    op.drop_column("objekt_kontakt", "benachrichtigung_mail")
    op.drop_column("objekt", "kontakt_info_template")
    op.drop_column("objekt", "kontakt_info_betreff")
    op.drop_column("objekt", "kontakt_info_stichworte")
    op.drop_column("objekt", "kontakt_info_uebung")
    op.drop_column("objekt", "kontakt_info_enabled")
