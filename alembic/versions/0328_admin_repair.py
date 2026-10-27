"""Kontrollierte globale Reparaturen und einmalige Freigaben."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import DATETIME

revision = "0328"
down_revision = "0327"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_repair_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("aktion_id", sa.String(80), nullable=False),
        sa.Column("diag_finding_id", sa.Integer(), sa.ForeignKey("diag_finding.id", ondelete="SET NULL")),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("risiko", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(64), unique=True),
        sa.Column("angefordert_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("simulation_hash", sa.String(64)),
        sa.Column("vorschau", sa.JSON(), nullable=False),
        sa.Column("ergebnis", sa.JSON(), nullable=False),
        sa.Column("verifikation", sa.JSON(), nullable=False),
        sa.Column("korrelations_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("simulated_at", sa.DateTime()),
        sa.Column("started_at", sa.DateTime().with_variant(DATETIME(fsp=6), "mysql", "mariadb")),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("active_aktion", sa.String(80), unique=True),
        sa.CheckConstraint("risiko IN ('low','medium','high','critical')", name="ck_repair_risiko"),
        sa.CheckConstraint(
            "status IN ('vorgeschlagen','simuliert','freigegeben','laufend','erfolgreich',"
            "'fehlgeschlagen','abgebrochen')",
            name="ck_repair_status",
        ),
        sa.CheckConstraint(
            "(status = 'laufend' AND active_aktion = aktion_id AND active_aktion IS NOT NULL) OR "
            "(status <> 'laufend' AND active_aktion IS NULL)",
            name="ck_repair_mutex",
        ),
    )
    op.create_table(
        "admin_approval",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "repair_run_id", sa.Integer(), sa.ForeignKey("admin_repair_run.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("aktion_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_admin_approval_repair_run_id", "admin_approval", ["repair_run_id"])
    if op.get_bind().dialect.name in ("mysql", "mariadb"):
        op.create_check_constraint(
            "ck_approval_lifetime",
            "admin_approval",
            "expires_at > created_at AND expires_at <= created_at + INTERVAL 10 MINUTE",
        )


def downgrade():
    op.drop_table("admin_approval")
    op.drop_table("admin_repair_run")
