"""ASUEW Phase 7: Atemschutzsammelplatz (nur Logistik)."""
import sqlalchemy as sa

from alembic import op

revision = "0354"
down_revision = "0353"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "breathing_assp",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("aktiv", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("leiter_name", sa.String(150), nullable=True),
        sa.Column("standort", sa.String(200), nullable=True),
        sa.Column("flaschen_voll", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flaschen_leer", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notiz", sa.String(300), nullable=True),
        sa.Column("aktiviert_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("incident_id", name="uq_breathing_assp_incident"),
    )


def downgrade() -> None:
    op.drop_table("breathing_assp")
