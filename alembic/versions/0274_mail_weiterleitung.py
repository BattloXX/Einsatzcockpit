"""Systemweite Mailweiterleitungen."""
import sqlalchemy as sa
from alembic import op
revision = "0274"
down_revision = "0273"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.create_table("mail_weiterleitung", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("adresse", sa.String(320), nullable=False), sa.Column("ziele", sa.Text(), nullable=False), sa.Column("modus", sa.String(10), nullable=False), sa.Column("aktiv", sa.Boolean(), nullable=False), sa.Column("beschreibung", sa.String(255)), sa.Column("max_pro_stunde", sa.Integer(), nullable=False), sa.Column("zaehler", sa.Integer(), nullable=False), sa.Column("letzte_weiterleitung_at", sa.DateTime()), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("adresse", name="uq_mail_weiterleitung_adresse"))
    op.create_table("mail_weiterleitung_log", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("weiterleitung_id", sa.Integer(), nullable=False), sa.Column("zeit", sa.DateTime(), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("absenderdomain", sa.String(253)), sa.Column("betreff", sa.String(255)), sa.Column("fehlercode", sa.String(64)), sa.Column("versuche", sa.Integer(), nullable=False), sa.Column("resend_email_id", sa.String(64)), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.ForeignKeyConstraint(["weiterleitung_id"], ["mail_weiterleitung.id"], ondelete="CASCADE"))
    op.create_index("ix_mail_weiterleitung_log_retry", "mail_weiterleitung_log", ["status", "updated_at"])
def downgrade() -> None:
    op.drop_table("mail_weiterleitung_log")
    op.drop_table("mail_weiterleitung")
