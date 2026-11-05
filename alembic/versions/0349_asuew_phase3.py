"""ASUEW Phase 3: App-Überwachungssitzungen und optionaler App-Zwang."""
import sqlalchemy as sa

from alembic import op

revision = "0349"
down_revision = "0348"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("org_settings") as batch:
        batch.add_column(sa.Column("asuew_app_pflicht", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "breathing_geraet_sitzung",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.BigInteger(), sa.ForeignKey("incident.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fcm_token_id", sa.BigInteger(), sa.ForeignKey("fcm_token.id", ondelete="SET NULL"), nullable=True),
        sa.Column("session_key", sa.String(64), unique=True, nullable=False),
        sa.Column("app_version", sa.String(30), nullable=True),
        sa.Column("platform", sa.String(10), nullable=False),
        sa.Column("geraet_label", sa.String(100), nullable=True),
        sa.Column("selbsttest", sa.Text(), nullable=True),
        sa.Column("akku_prozent", sa.Integer(), nullable=True),
        sa.Column("laedt", sa.Boolean(), nullable=True),
        sa.Column("gestartet_at", sa.DateTime(), nullable=False),
        sa.Column("letzter_heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("beendet_at", sa.DateTime(), nullable=True),
        sa.Column("ende_grund", sa.String(20), nullable=True),
    )
    op.create_index("ix_breathing_sitzung_incident_ende_heartbeat", "breathing_geraet_sitzung",
                    ["incident_id", "beendet_at", "letzter_heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_breathing_sitzung_incident_ende_heartbeat", table_name="breathing_geraet_sitzung")
    op.drop_table("breathing_geraet_sitzung")
    with op.batch_alter_table("org_settings") as batch:
        batch.drop_column("asuew_app_pflicht")
