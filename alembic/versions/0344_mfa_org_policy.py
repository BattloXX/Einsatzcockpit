"""MFA-Richtlinien pro Organisation und Zeitpunkt des Benutzer-Resets."""
import sqlalchemy as sa

from alembic import op

revision = "0344"
down_revision = "0343"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("org_settings", sa.Column("mfa_policy", sa.String(20), nullable=False, server_default="aus"))
    op.add_column("org_settings", sa.Column("mfa_sms_erlaubt", sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("org_settings", sa.Column("mfa_sso_ausgenommen", sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("user", sa.Column("mfa_reset_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "mfa_reset_at")
    op.drop_column("org_settings", "mfa_sso_ausgenommen")
    op.drop_column("org_settings", "mfa_sms_erlaubt")
    op.drop_column("org_settings", "mfa_policy")
