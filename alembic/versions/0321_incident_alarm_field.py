"""Herkunftsschutz fuer Alarmfelder."""
import sqlalchemy as sa

from alembic import op

revision = "0321"
down_revision = "0320"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_alarm_field",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("ingest_id", sa.BigInteger(), sa.ForeignKey("alarm_ingest.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("incident_id", "field", name="uq_incident_alarm_field"),
    )
    op.create_index("ix_incident_alarm_field_org_id", "incident_alarm_field", ["org_id"])


def downgrade() -> None:
    op.drop_table("incident_alarm_field")
