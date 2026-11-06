"""ASUEW Phase 5: idempotente Offline-Ereignisse."""
import sqlalchemy as sa

from alembic import op

revision = "0350"
down_revision = "0349"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "breathing_client_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("client_event_id", sa.String(40), nullable=False),
        sa.Column("route", sa.String(30), nullable=False),
        sa.Column("erfasst_at", sa.DateTime(), nullable=True),
        sa.Column("empfangen_at", sa.DateTime(), nullable=False),
        sa.Column("nachgetragen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("incident_id", "client_event_id", name="uq_breathing_client_event_incident"),
    )
    op.create_index("ix_breathing_client_event_empfangen_at", "breathing_client_event", ["empfangen_at"])
    for table in ("pressure_log", "breathing_kontrolle"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("nachgetragen", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for table in ("breathing_kontrolle", "pressure_log"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("nachgetragen")
    op.drop_index("ix_breathing_client_event_empfangen_at", table_name="breathing_client_event")
    op.drop_table("breathing_client_event")
