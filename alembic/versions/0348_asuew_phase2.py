"""ASUEW Phase 2: Warnungen, Zustellungen und Eskalationseinstellungen."""
import sqlalchemy as sa

from alembic import op

revision = "0348"
down_revision = "0347"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.add_column(sa.Column("asuew_eskalation_user_ids", sa.Text(), nullable=True))
        batch.add_column(sa.Column("asuew_eskalation_sms", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "breathing_warnung",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("troop_id", sa.BigInteger(), sa.ForeignKey("breathing_troop.id", ondelete="CASCADE"), nullable=False),
        sa.Column("zyklus_start", sa.DateTime(), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("ausgeloest_at", sa.DateTime(), nullable=False),
        sa.Column("erledigt_at", sa.DateTime(), nullable=True),
        sa.Column("eskalation_stufe", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eskaliert_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_breathing_warnung_troop_kind_erledigt", "breathing_warnung", ["troop_id", "kind", "erledigt_at"])
    op.create_table(
        "breathing_zustellung",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("warnung_id", sa.BigInteger(), sa.ForeignKey("breathing_warnung.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stufe", sa.Integer(), nullable=False),
        sa.Column("kanal", sa.String(8), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("empfaenger_label", sa.String(150), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("versuche", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("naechster_versuch_at", sa.DateTime(), nullable=True),
        sa.Column("lease_bis", sa.DateTime(), nullable=True),
        sa.Column("fehler", sa.String(300), nullable=True),
        sa.Column("gesendet_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("warnung_id", "stufe", "kanal", "user_id", name="uq_breathing_zustellung_empfaenger"),
    )
    op.create_index("ix_breathing_zustellung_status_versuch", "breathing_zustellung", ["status", "naechster_versuch_at"])


def downgrade() -> None:
    op.drop_index("ix_breathing_zustellung_status_versuch", table_name="breathing_zustellung")
    op.drop_table("breathing_zustellung")
    op.drop_index("ix_breathing_warnung_troop_kind_erledigt", table_name="breathing_warnung")
    op.drop_table("breathing_warnung")
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("asuew_eskalation_sms")
        batch.drop_column("asuew_eskalation_user_ids")
