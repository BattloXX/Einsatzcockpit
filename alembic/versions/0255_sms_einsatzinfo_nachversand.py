"""Persistente Einsatzinfo-SMS-Nachversandzuordnung.

Revision ID: 0255
Revises: 0254
"""
import sqlalchemy as sa
from alembic import op

revision = "0255"
down_revision = "0254"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sms_log") as batch:
        batch.add_column(sa.Column("incident_id", sa.BigInteger(), nullable=True))
        batch.create_foreign_key(
            "fk_sms_log_incident", "incident", ["incident_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_sms_log_incident_id", ["incident_id"])


def downgrade() -> None:
    with op.batch_alter_table("sms_log") as batch:
        batch.drop_index("ix_sms_log_incident_id")
        batch.drop_constraint("fk_sms_log_incident", type_="foreignkey")
        batch.drop_column("incident_id")
