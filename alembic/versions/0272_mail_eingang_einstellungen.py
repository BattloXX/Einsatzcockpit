"""Mail-Eingangseinstellungen je Organisation.

Revision ID: 0272
Revises: 0271
"""
import sqlalchemy as sa
from alembic import op

revision = "0272"
down_revision = "0271"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_settings", sa.Column("mail_eingang_modus", sa.String(12), nullable=False, server_default="aus"))
    op.add_column("org_settings", sa.Column("mail_eingang_rueckblick_min", sa.Integer(), nullable=False, server_default="30"))
    op.add_column("org_settings", sa.Column("mail_eingang_mehrere", sa.String(12), nullable=False, server_default="posteingang"))
    op.add_column("org_settings", sa.Column("mail_eingang_absender", sa.Text(), nullable=True))
    op.add_column("org_settings", sa.Column("mail_eingang_max_pro_stunde", sa.Integer(), nullable=False, server_default="30"))


def downgrade() -> None:
    op.drop_column("org_settings", "mail_eingang_max_pro_stunde")
    op.drop_column("org_settings", "mail_eingang_absender")
    op.drop_column("org_settings", "mail_eingang_mehrere")
    op.drop_column("org_settings", "mail_eingang_rueckblick_min")
    op.drop_column("org_settings", "mail_eingang_modus")
