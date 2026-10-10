"""GSL Personal, Ausstattung und Verbände.

Revision ID: 0267
Revises: 0266
"""

import sqlalchemy as sa

from alembic import op

revision = "0267"
down_revision = "0266"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, column in (
        ("staerke_gesamt", sa.Column("staerke_gesamt", sa.Integer(), nullable=True)),
        ("staerke_fuehrung", sa.Column("staerke_fuehrung", sa.Integer(), nullable=True)),
        ("staerke_agt", sa.Column("staerke_agt", sa.Integer(), nullable=True)),
        ("staerke_sanitaeter", sa.Column("staerke_sanitaeter", sa.Integer(), nullable=True)),
        (
            "personal_modus",
            sa.Column("personal_modus", sa.String(8), nullable=False, server_default=sa.text("'summe'")),
        ),
        ("personal_bemerkung", sa.Column("personal_bemerkung", sa.Text(), nullable=True)),
        ("verband_id", sa.Column("verband_id", sa.Integer(), nullable=True)),
        ("aufgeteilt_von_id", sa.Column("aufgeteilt_von_id", sa.Integer(), nullable=True)),
    ):
        op.add_column("lage_einheit", column)
    with op.batch_alter_table("lage_einheit") as batch:
        batch.create_foreign_key("fk_le_verband", "lage_einheit", ["verband_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key(
            "fk_le_aufgeteilt_von", "lage_einheit", ["aufgeteilt_von_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_lage_einheit_verband_id", "lage_einheit", ["verband_id"])

    op.create_table(
        "lage_einheit_person",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lage_id", sa.Integer(), sa.ForeignKey("major_incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("einheit_id", sa.Integer(), sa.ForeignKey("lage_einheit.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.BigInteger(), sa.ForeignKey("member.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("funktion", sa.String(16), nullable=False),
        sa.Column("qualifikationen", sa.String(200), nullable=True),
        sa.Column("von_at", sa.DateTime(), nullable=False),
        sa.Column("bis_at", sa.DateTime(), nullable=True),
        sa.Column("herkunft", sa.String(14), nullable=False),
        sa.Column(
            "abloesung_von_id", sa.BigInteger(), sa.ForeignKey("lage_einheit_person.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("umbuchung_id", sa.String(36), nullable=True),
        sa.Column("bemerkung", sa.String(300), nullable=True),
        sa.Column("aktiv_key", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("lage_id", "aktiv_key", name="uq_lage_einheit_person_aktiv"),
    )
    op.create_index("ix_lage_einheit_person_org_id", "lage_einheit_person", ["org_id"])
    op.create_index("ix_lage_einheit_person_lage_id", "lage_einheit_person", ["lage_id"])
    op.create_index("ix_lage_einheit_person_einheit_id", "lage_einheit_person", ["einheit_id"])

    op.create_table(
        "lage_einheit_ausstattung",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lage_id", sa.Integer(), sa.ForeignKey("major_incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("einheit_id", sa.Integer(), sa.ForeignKey("lage_einheit.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kategorie", sa.String(24), nullable=False),
        sa.Column("bezeichnung", sa.String(120), nullable=False),
        sa.Column("ist_faehigkeit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("menge", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'einsatzbereit'")),
        sa.Column("bemerkung", sa.String(300), nullable=True),
        sa.Column("stamm_ref_typ", sa.String(24), nullable=True),
        sa.Column("stamm_ref_id", sa.BigInteger(), nullable=True),
        sa.Column("umbuchung_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_lage_einheit_ausstattung_org_id", "lage_einheit_ausstattung", ["org_id"])
    op.create_index("ix_lage_einheit_ausstattung_lage_id", "lage_einheit_ausstattung", ["lage_id"])
    op.create_index("ix_lage_einheit_ausstattung_einheit_id", "lage_einheit_ausstattung", ["einheit_id"])


def downgrade() -> None:
    # MariaDB requires foreign keys to be removed before their supporting indexes.
    op.drop_table("lage_einheit_ausstattung")
    op.drop_table("lage_einheit_person")
    with op.batch_alter_table("lage_einheit") as batch:
        batch.drop_constraint("fk_le_aufgeteilt_von", type_="foreignkey")
        batch.drop_constraint("fk_le_verband", type_="foreignkey")
    op.drop_index("ix_lage_einheit_verband_id", table_name="lage_einheit")
    for column in (
        "aufgeteilt_von_id", "verband_id", "personal_bemerkung", "personal_modus",
        "staerke_sanitaeter", "staerke_agt", "staerke_fuehrung", "staerke_gesamt",
    ):
        op.drop_column("lage_einheit", column)
