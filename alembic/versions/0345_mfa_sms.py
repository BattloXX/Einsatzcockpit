"""SMS als alternativer zweiter Faktor."""
from alembic import op
import sqlalchemy as sa

revision = "0345"
down_revision = "0344"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_mfa_sms",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("phone_enc", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.add_column("login_pin", sa.Column("purpose", sa.String(12), nullable=False, server_default="login"))


def downgrade() -> None:
    op.drop_column("login_pin", "purpose")
    op.drop_table("user_mfa_sms")
