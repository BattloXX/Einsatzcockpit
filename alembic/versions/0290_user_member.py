"""Verknuepfung zwischen Benutzer und Mitglied.

Revision ID: 0290
Revises: 0289
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0290"
down_revision = "0289"
branch_labels = None
depends_on = None


def _tabellen(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _spalten(bind, tabelle: str) -> set[str]:
    if tabelle not in _tabellen(bind):
        return set()
    return {spalte["name"] for spalte in sa.inspect(bind).get_columns(tabelle)}


def _indizes(bind, tabelle: str) -> set[str]:
    if tabelle not in _tabellen(bind):
        return set()
    return {index["name"] for index in sa.inspect(bind).get_indexes(tabelle) if index.get("name")}


def _fremdschluessel(bind, tabelle: str) -> set[str]:
    if tabelle not in _tabellen(bind):
        return set()
    return {
        schluessel["name"] for schluessel in sa.inspect(bind).get_foreign_keys(tabelle)
        if schluessel.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    user_spalten = _spalten(bind, "user")
    user_fks = _fremdschluessel(bind, "user")
    if "member_id" not in user_spalten or "fk_user_member" not in user_fks:
        with op.batch_alter_table("user") as batch:
            if "member_id" not in user_spalten:
                batch.add_column(sa.Column("member_id", sa.BigInteger(), nullable=True))
            if "fk_user_member" not in user_fks:
                batch.create_foreign_key(
                    "fk_user_member", "member", ["member_id"], ["id"], ondelete="SET NULL"
                )
    if "uq_user_member_id" not in _indizes(bind, "user"):
        op.create_index("uq_user_member_id", "user", ["member_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if "uq_user_member_id" in _indizes(bind, "user"):
        op.drop_index("uq_user_member_id", table_name="user")
    user_spalten = _spalten(bind, "user")
    user_fks = _fremdschluessel(bind, "user")
    if "member_id" in user_spalten:
        with op.batch_alter_table("user") as batch:
            if "fk_user_member" in user_fks:
                batch.drop_constraint("fk_user_member", type_="foreignkey")
            batch.drop_column("member_id")
