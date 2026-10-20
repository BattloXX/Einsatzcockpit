"""Zentrale Suche: Dokumente, Chunks und Index-Warteschlange.

Revision ID: 0300
Revises: 0299
"""

import sqlalchemy as sa

from alembic import op

revision = "0300"
down_revision = "0299"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_document",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quelle", sa.String(40), nullable=False),
        sa.Column("quell_id", sa.String(64), nullable=False),
        sa.Column("scope_typ", sa.String(20), nullable=False, server_default="org"),
        sa.Column("scope_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("titel", sa.String(255), nullable=False),
        sa.Column("untertitel", sa.String(255)),
        sa.Column("search_keywords", sa.Text()),
        sa.Column("sichtbarkeit", sa.String(20), nullable=False, server_default="standard"),
        sa.Column("lat", sa.Float()),
        sa.Column("lng", sa.Float()),
        sa.Column("meta_json", sa.Text()),
        sa.Column("inhalt_hash", sa.String(64), nullable=False),
        sa.Column("quelle_geaendert_am", sa.DateTime()),
        sa.Column("indexiert_am", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("org_id", "quelle", "quell_id", "scope_typ", "scope_id",
                            name="uq_search_document_quelle"),
    )
    op.create_index("ix_search_document_org_quelle", "search_document", ["org_id", "quelle"])
    op.create_table(
        "search_chunk",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.BigInteger(), sa.ForeignKey("search_document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_nr", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("seite", sa.Integer()),
        sa.Column("abschnitt", sa.String(255)),
        sa.Column("inhalt_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_nr", name="uq_search_chunk_nr"),
    )
    op.create_index("ix_search_chunk_org_doc", "search_chunk", ["org_id", "document_id"])
    op.create_table(
        "search_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quelle", sa.String(40), nullable=False),
        sa.Column("quell_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("versuche", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("naechster_versuch_am", sa.DateTime(), nullable=False),
        sa.Column("gesperrt_bis", sa.DateTime()),
        sa.Column("letzter_fehler", sa.String(500)),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_search_outbox_faellig", "search_outbox", ["status", "naechster_versuch_am"])
    op.create_index("ix_search_outbox_quelle", "search_outbox", ["org_id", "quelle", "quell_id", "status"])
    op.add_column("org_settings", sa.Column("suche_aktiv", sa.Boolean(), nullable=False, server_default=sa.false()))
    if op.get_bind().dialect.name in ("mysql", "mariadb"):
        op.execute("CREATE FULLTEXT INDEX ft_search_chunk_text ON search_chunk (text)")
        op.execute("CREATE FULLTEXT INDEX ft_search_document_text ON search_document (titel, search_keywords)")


def downgrade() -> None:
    if op.get_bind().dialect.name in ("mysql", "mariadb"):
        op.drop_index("ft_search_document_text", table_name="search_document")
        op.drop_index("ft_search_chunk_text", table_name="search_chunk")
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("suche_aktiv")
    op.drop_table("search_outbox")
    op.drop_table("search_chunk")
    op.drop_table("search_document")
