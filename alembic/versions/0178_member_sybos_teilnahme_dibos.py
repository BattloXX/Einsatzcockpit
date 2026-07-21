"""Member.sybos_id + Teilnahme.dibos_response_id (DIBOS-Personenrückmeldungen)

Ergaenzt:
- member.sybos_id — syBOS-Personen-ID aus dem Mitglieder-Excel-Import (Spalte
  "syBOS-ID"), stabiler Schluessel um DIBOS-EventHub-Personenrueckmeldungen
  (personResponseList[].idSybos) einem Mitglied zuzuordnen.
- teilnahme.dibos_response_id + Unique-Constraint (org_id, dibos_response_id) —
  Upsert-Schluessel fuer Personenrueckmeldungen OHNE Mitglied-Zuordnung (kein
  sybos_id-Match), da die bestehende Constraint (org_id, bezug_typ, bezug_id,
  mitglied_id) bei mitglied_id=NULL keine Dedup-Wirkung hat (NULL zaehlt in
  MySQL/SQLite je einzeln als eindeutig).

Siehe app/services/dibos/dibos_enrich.py.

Revision ID: 0178
Revises: 0177
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "0178"
down_revision = "0177"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    member_cols = {c["name"] for c in sa_inspect(bind).get_columns("member")}
    if "sybos_id" not in member_cols:
        op.add_column("member", sa.Column("sybos_id", sa.String(50), nullable=True))
        op.create_index("ix_member_sybos_id", "member", ["sybos_id"])

    teilnahme_cols = {c["name"] for c in sa_inspect(bind).get_columns("teilnahme")}
    if "dibos_response_id" not in teilnahme_cols:
        op.add_column("teilnahme", sa.Column("dibos_response_id", sa.BigInteger(), nullable=True))
        op.create_index("ix_teilnahme_dibos_response_id", "teilnahme", ["dibos_response_id"])

    existing_uniques = {uc["name"] for uc in sa_inspect(bind).get_unique_constraints("teilnahme")}
    if "uq_teilnahme_dibos_response" not in existing_uniques:
        op.create_unique_constraint(
            "uq_teilnahme_dibos_response", "teilnahme", ["org_id", "dibos_response_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    existing_uniques = {uc["name"] for uc in sa_inspect(bind).get_unique_constraints("teilnahme")}
    if "uq_teilnahme_dibos_response" in existing_uniques:
        op.drop_constraint("uq_teilnahme_dibos_response", "teilnahme", type_="unique")

    teilnahme_cols = {c["name"] for c in sa_inspect(bind).get_columns("teilnahme")}
    if "dibos_response_id" in teilnahme_cols:
        op.drop_index("ix_teilnahme_dibos_response_id", table_name="teilnahme")
        op.drop_column("teilnahme", "dibos_response_id")

    member_cols = {c["name"] for c in sa_inspect(bind).get_columns("member")}
    if "sybos_id" in member_cols:
        op.drop_index("ix_member_sybos_id", table_name="member")
        op.drop_column("member", "sybos_id")
