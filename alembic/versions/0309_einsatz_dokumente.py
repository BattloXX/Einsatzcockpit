"""Dokumentübernahmen: unabhängige Fassungen und explizite Empfänger."""

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB

from alembic import op

revision = "0309"
down_revision = "0308"
branch_labels = None
depends_on = None


def _org():
    return sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True)


def upgrade():
    op.create_table("einsatz_dokument_fassung",
        sa.Column("id", sa.Integer(), primary_key=True), _org(),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("inhalt", sa.LargeBinary().with_variant(LONGBLOB(), "mysql", "mariadb"), nullable=False),
        sa.UniqueConstraint("org_id", "sha256", name="uq_einsatz_fassung_hash"))
    op.create_table("einsatz_dokument",
        sa.Column("id", sa.Integer(), primary_key=True), _org(),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE")),
        sa.Column("lage_id", sa.Integer(), sa.ForeignKey("major_incident.id", ondelete="CASCADE")),
        sa.Column("fassung_id", sa.Integer(), sa.ForeignKey("einsatz_dokument_fassung.id"), nullable=False),
        sa.Column("titel", sa.String(300), nullable=False),
        sa.Column("quell_url", sa.String(2048), nullable=False),
        sa.Column("quelle_json", sa.Text(), nullable=False),
        sa.Column("seiten", sa.Text(), nullable=False),
        sa.Column("seitenzahl", sa.Integer(), nullable=False),
        sa.Column("vertraulich", sa.Boolean(), nullable=False),
        sa.Column("uebernommen_am", sa.DateTime(), nullable=False),
        sa.Column("uebernommen_von_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("task.id", ondelete="SET NULL")),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("incident_site.id", ondelete="SET NULL")),
        sa.Column("auftrag_freigegeben", sa.Boolean(), nullable=False),
        sa.Column("entfernt_am", sa.DateTime()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("(incident_id IS NOT NULL AND lage_id IS NULL) OR "
                           "(incident_id IS NULL AND lage_id IS NOT NULL)", name="ck_einsatz_dokument_ziel"))
    op.create_table("einsatz_dokument_empfaenger",
        sa.Column("id", sa.Integer(), primary_key=True), _org(),
        sa.Column("dokument_id", sa.Integer(), sa.ForeignKey("einsatz_dokument.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("einheit_id", sa.Integer(), sa.ForeignKey("lage_einheit.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("dokument_id", "einheit_id", name="uq_einsatz_dokument_empfaenger"))
    for table in ("einsatz_dokument_fassung", "einsatz_dokument", "einsatz_dokument_empfaenger"):
        op.create_index("ix_" + table + "_org_id", table, ["org_id"])
    op.create_index("ix_einsatz_dokument_incident", "einsatz_dokument", ["incident_id"])
    op.create_index("ix_einsatz_dokument_lage", "einsatz_dokument", ["lage_id"])


def downgrade():
    op.drop_table("einsatz_dokument_empfaenger")
    op.drop_table("einsatz_dokument")
    op.drop_table("einsatz_dokument_fassung")
