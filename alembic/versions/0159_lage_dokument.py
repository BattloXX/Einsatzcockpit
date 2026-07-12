"""Lagedokument: gemeinsam bearbeitbares Dokument je Lage (Word-Online-artig)

Neue eigenstaendige Tabelle, unabhaengig vom Einsatzjournal (das bleibt
Append-only) UND vom bestehenden "KI-Lagebericht" (KI-generierte Einmal-
Zusammenfassung, POST /lage/{id}/lagebericht in ui_major_incident.py) --
Namenskollision bewusst vermieden, daher "Lagedokument" statt "Lagebericht".
content_html haelt den letzten sanitisierten HTML-Snapshot fuer Druck/Export/
Fallback; ydoc_state den vollstaendigen Yjs-CRDT-Stand fuer die Live-
Kollaboration (ab PR 2 genutzt, hier bereits angelegt).

Revision ID: 0159
Revises: 0158
Create Date: 2026-07-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "0159"
down_revision = "0158"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "lage_dokument" not in sa_inspect(bind).get_table_names():
        op.create_table(
            "lage_dokument",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("major_incident_id", sa.Integer(),
                      sa.ForeignKey("major_incident.id", ondelete="CASCADE"),
                      unique=True, nullable=False),
            sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id"), nullable=False),
            sa.Column("content_html", sa.Text(), nullable=True),
            sa.Column("ydoc_state", sa.LargeBinary(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("updated_by_user_id", sa.BigInteger(),
                      sa.ForeignKey("user.id"), nullable=True),
        )
        op.create_index("ix_lage_dokument_org_id", "lage_dokument", ["org_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if "lage_dokument" in sa_inspect(bind).get_table_names():
        op.drop_table("lage_dokument")
