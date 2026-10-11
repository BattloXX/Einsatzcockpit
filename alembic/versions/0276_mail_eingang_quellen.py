"""Quellen und Ereignisse fuer den Mail-Eingang.

Revision ID: 0276
Revises: 0275
"""
import sqlalchemy as sa
from alembic import op

revision = "0276"
down_revision = "0275"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("org_mail_eingang", naming_convention={"uq": "uq_org_mail_eingang_resend"}) as batch:
        batch.drop_constraint("uq_org_mail_eingang_resend", type_="unique")
        batch.add_column(sa.Column("quelle", sa.String(10), nullable=False, server_default="resend"))
        batch.alter_column("resend_email_id", new_column_name="extern_id", existing_type=sa.String(64), type_=sa.String(255),
                            existing_nullable=False, nullable=False)
        batch.create_unique_constraint("uq_org_mail_eingang_quelle", ["org_id", "quelle", "extern_id"])
        batch.add_column(sa.Column("anhang_speicher", sa.String(10), nullable=True))
        batch.add_column(sa.Column("groesse_bytes", sa.Integer(), nullable=True))
    op.create_table(
        "org_mail_eingang_ereignis",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("eingang_id", sa.Integer(), sa.ForeignKey("org_mail_eingang.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False), sa.Column("typ", sa.String(32), nullable=False),
        sa.Column("text", sa.String(500), nullable=True), sa.Column("user_id", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_org_mail_eingang_ereignis_eingang_ts", "org_mail_eingang_ereignis", ["eingang_id", "ts"])
    op.add_column("org_smtp_config", sa.Column("imap_ordner", sa.String(200), nullable=False, server_default="INBOX"))
    op.add_column("org_smtp_config", sa.Column("imap_uidvalidity", sa.BigInteger(), nullable=True))
    op.add_column("org_smtp_config", sa.Column("imap_letzte_uid", sa.BigInteger(), nullable=True))
    op.add_column("org_smtp_config", sa.Column("imap_letzter_abruf_at", sa.DateTime(), nullable=True))
    op.add_column("org_smtp_config", sa.Column("imap_letzter_fehler", sa.String(255), nullable=True))
    op.add_column("org_smtp_config", sa.Column("imap_aktiv_seit", sa.DateTime(), nullable=True))
    op.add_column("org_o365_mail_config", sa.Column("read_postfach", sa.String(320), nullable=True))
    op.add_column("org_o365_mail_config", sa.Column("read_seit", sa.DateTime(), nullable=True))
    op.add_column("org_o365_mail_config", sa.Column("read_letzter_abruf_at", sa.DateTime(), nullable=True))
    op.add_column("org_o365_mail_config", sa.Column("read_letzter_fehler", sa.String(255), nullable=True))
    op.add_column("org_o365_mail_config", sa.Column("read_aktiv_seit", sa.DateTime(), nullable=True))
    op.add_column("org_settings", sa.Column("mail_eingang_aufbewahrung_tage", sa.Integer(), nullable=False, server_default="90"))
    if bind.dialect.name == "sqlite":
        op.execute("UPDATE org_settings SET mail_eingang_aufbewahrung_tage = (SELECT inbound_retention_days FROM org_resend_mail_config WHERE org_resend_mail_config.org_id = org_settings.org_id) WHERE EXISTS (SELECT 1 FROM org_resend_mail_config WHERE org_resend_mail_config.org_id = org_settings.org_id)")
    else:
        op.execute("UPDATE org_settings JOIN org_resend_mail_config ON org_resend_mail_config.org_id = org_settings.org_id SET org_settings.mail_eingang_aufbewahrung_tage = org_resend_mail_config.inbound_retention_days")


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_column("org_settings", "mail_eingang_aufbewahrung_tage")
    for column in ("read_aktiv_seit", "read_letzter_fehler", "read_letzter_abruf_at", "read_seit", "read_postfach"):
        op.drop_column("org_o365_mail_config", column)
    for column in ("imap_aktiv_seit", "imap_letzter_fehler", "imap_letzter_abruf_at", "imap_letzte_uid", "imap_uidvalidity", "imap_ordner"):
        op.drop_column("org_smtp_config", column)
    op.drop_index("ix_org_mail_eingang_ereignis_eingang_ts", table_name="org_mail_eingang_ereignis")
    op.drop_table("org_mail_eingang_ereignis")
    with op.batch_alter_table("org_mail_eingang", naming_convention={"uq": "uq_org_mail_eingang_quelle"}) as batch:
        batch.drop_column("groesse_bytes")
        batch.drop_column("anhang_speicher")
        batch.alter_column("extern_id", new_column_name="resend_email_id", existing_type=sa.String(255), type_=sa.String(64),
                            existing_nullable=False, nullable=False)
        batch.create_unique_constraint("uq_org_mail_eingang_resend", ["org_id", "resend_email_id"])
        batch.drop_column("quelle")
