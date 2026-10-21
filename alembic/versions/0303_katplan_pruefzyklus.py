"""Pruefzyklus mit verantwortlicher Person.

Revision ID: 0303
Revises: 0302
"""

import sqlalchemy as sa

from alembic import op

revision = "0303"
down_revision = "0302"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("katplan_plan") as batch:
        for name in ("pruef_verantwortlich_user_id", "zuletzt_geprueft_von_id"):
            batch.add_column(sa.Column(name, sa.BigInteger, nullable=True))
            batch.create_foreign_key("fk_katplan_plan_" + name, "user", [name], ["id"], ondelete="SET NULL")
        batch.add_column(sa.Column("pruefintervall_monate", sa.Integer, nullable=True))
        for name in ("zuletzt_geprueft_am", "wiedervorlage_vorab_erinnert_am", "wiedervorlage_eskaliert_am"):
            batch.add_column(sa.Column(name, sa.Date, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("katplan_plan") as batch:
        for name in ("pruef_verantwortlich_user_id", "zuletzt_geprueft_von_id"):
            batch.drop_constraint("fk_katplan_plan_" + name, type_="foreignkey")
            batch.drop_column(name)
        for name in ("pruefintervall_monate", "zuletzt_geprueft_am",
                     "wiedervorlage_vorab_erinnert_am", "wiedervorlage_eskaliert_am"):
            batch.drop_column(name)
