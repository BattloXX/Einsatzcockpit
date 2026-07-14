"""fahrt_media: Fotos zu einer Schadensmeldung im Fahrtenbuch

Neue eigenstaendige Tabelle (Muster: task_media, siehe app/models/incident.py) --
bewusst OHNE incident_id (eine Fahrt hat keinen zwingenden Einsatzbezug). Fotos
werden im Fahrten-Formular bei "Schaden vorhanden" optional hochgeladen und der
Schadenmeldung (Mail/Teams, siehe schaden_service.py) als Bild beigefuegt.

Revision ID: 0161
Revises: 0160
Create Date: 2026-07-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "0161"
down_revision = "0160"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "fahrt_media" not in sa_inspect(bind).get_table_names():
        op.create_table(
            "fahrt_media",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("fahrt_id", sa.BigInteger(),
                      sa.ForeignKey("fahrt.id", ondelete="CASCADE"), nullable=False),
            sa.Column("org_id", sa.BigInteger(),
                      sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False),
            sa.Column("uploaded_by_user_id", sa.BigInteger(),
                      sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("original_filename", sa.String(255), nullable=False),
            sa.Column("storage_path", sa.String(500), nullable=False),
            sa.Column("thumb_path", sa.String(500), nullable=True),
            sa.Column("mime_type", sa.String(100), nullable=False),
            sa.Column("bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("width", sa.Integer(), nullable=True),
            sa.Column("height", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
            mysql_charset="utf8mb4",
            mysql_engine="InnoDB",
        )
        op.create_index("ix_fahrt_media_fahrt_id", "fahrt_media", ["fahrt_id"])
        op.create_index("ix_fahrt_media_created_at", "fahrt_media", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if "fahrt_media" in sa_inspect(bind).get_table_names():
        op.drop_table("fahrt_media")
