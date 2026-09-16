"""Add object document versioning.

Revision ID: 0248
Revises: 0247
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0248"
down_revision = "0247"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("objekt_dokument", sa.Column("dokument_gruppe_id", sa.BigInteger(), nullable=True))
    op.add_column("objekt_dokument", sa.Column("versionsnummer", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("objekt_dokument", sa.Column("freigabe_status", sa.String(length=20), nullable=False, server_default="freigegeben"))
    op.add_column("objekt_dokument", sa.Column("ist_aktuelle_version", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("objekt_dokument", sa.Column("pflegeauftrag_id", sa.BigInteger(), nullable=True))
    op.add_column("objekt_dokument", sa.Column("freigegeben_am", sa.DateTime(), nullable=True))
    op.add_column("objekt_dokument", sa.Column("freigegeben_von_id", sa.BigInteger(), nullable=True))
    ist_mysql = op.get_bind().dialect.name == "mysql"
    if ist_mysql:
        op.create_foreign_key("fk_objekt_dokument_gruppe", "objekt_dokument", "objekt_dokument", ["dokument_gruppe_id"], ["id"], ondelete="SET NULL")
        op.create_foreign_key("fk_objekt_dokument_pflegeauftrag", "objekt_dokument", "objekt_pflegeauftrag", ["pflegeauftrag_id"], ["id"], ondelete="SET NULL")
        op.create_foreign_key("fk_objekt_dokument_freigegeben_von", "objekt_dokument", "user", ["freigegeben_von_id"], ["id"], ondelete="SET NULL")
    op.alter_column("objekt_dokument", "versionsnummer", server_default=None)
    op.alter_column("objekt_dokument", "freigabe_status", server_default=None)
    op.alter_column("objekt_dokument", "ist_aktuelle_version", server_default=None)


def downgrade() -> None:
    ist_mysql = op.get_bind().dialect.name == "mysql"
    if ist_mysql:
        op.drop_constraint("fk_objekt_dokument_freigegeben_von", "objekt_dokument", type_="foreignkey")
        op.drop_constraint("fk_objekt_dokument_pflegeauftrag", "objekt_dokument", type_="foreignkey")
        op.drop_constraint("fk_objekt_dokument_gruppe", "objekt_dokument", type_="foreignkey")
    op.drop_column("objekt_dokument", "freigegeben_von_id")
    op.drop_column("objekt_dokument", "freigegeben_am")
    op.drop_column("objekt_dokument", "pflegeauftrag_id")
    op.drop_column("objekt_dokument", "ist_aktuelle_version")
    op.drop_column("objekt_dokument", "freigabe_status")
    op.drop_column("objekt_dokument", "versionsnummer")
    op.drop_column("objekt_dokument", "dokument_gruppe_id")
