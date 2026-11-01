"""ActivityKit-Push-Tokens."""
import sqlalchemy as sa

from alembic import op

revision = "0341"
down_revision = "0340"
branch_labels = None
depends_on = None


def _fk_typ(tabelle: str) -> sa.types.TypeEngine:
    # MariaDB verlangt identische Groesse und Signedness, auch bei Altinstallationen.
    if not op.get_context().as_sql:
        for spalte in sa.inspect(op.get_bind()).get_columns(tabelle):
            if spalte["name"] == "id":
                return spalte["type"]
    return sa.BigInteger()


def upgrade() -> None:
    op.create_table(
        "live_activity_token",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("user_id", _fk_typ("user"), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_token_id", _fk_typ("device_token"),
                  sa.ForeignKey("device_token.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("token", sa.String(512), unique=True, nullable=False),
        sa.Column("incident_id", _fk_typ("incident"),
                  sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.String(120), nullable=True),
    )
    op.create_index("ix_live_activity_token_user_id", "live_activity_token", ["user_id"])
    op.create_index("ix_live_activity_token_incident_id", "live_activity_token", ["incident_id"])


def downgrade() -> None:
    op.drop_table("live_activity_token")
