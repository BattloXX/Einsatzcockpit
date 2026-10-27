"""Globale Diagnose-Laeufe und deduplizierte Befunde."""
import sqlalchemy as sa
from alembic import op

revision = "0327"
down_revision = "0326"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("diag_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("umfang", sa.JSON(), nullable=False), sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("angefordert_von", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("quelle", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime()), sa.Column("finished_at", sa.DateTime()),
        sa.Column("fortschritt_am", sa.DateTime(), nullable=False),
        sa.Column("fortschritt", sa.JSON(), nullable=False), sa.Column("fehlertext", sa.Text()),
        sa.Column("korrelations_id", sa.String(36), nullable=False, unique=True),
        sa.Column("active_slot", sa.Integer(), unique=True),
        sa.CheckConstraint("status IN ('queued','running','done','failed','cancelled')", name="ck_diag_run_status"),
        sa.CheckConstraint("quelle IN ('manual','mcp','schedule')", name="ck_diag_run_quelle"),
        sa.CheckConstraint("active_slot IS NULL OR active_slot = 1", name="ck_diag_run_slot"),
        sa.CheckConstraint("(status IN ('queued','running') AND active_slot IS NOT NULL AND active_slot = 1) OR "
                           "(status IN ('done','failed','cancelled') AND active_slot IS NULL)",
                           name="ck_diag_run_active"))
    for column in ("status", "created_at"):
        op.create_index("ix_diag_run_" + column, "diag_run", [column])
    op.create_table("diag_finding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("diag_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("finding_key", sa.String(64), nullable=False, unique=True),
        sa.Column("check_id", sa.String(80), nullable=False), sa.Column("modul", sa.String(40), nullable=False),
        sa.Column("schweregrad", sa.String(10), nullable=False), sa.Column("titel", sa.String(200), nullable=False),
        sa.Column("evidenz", sa.JSON(), nullable=False), sa.Column("betroffene_ids", sa.JSON(), nullable=False),
        sa.Column("ursache_hypothese", sa.String(500), nullable=False),
        sa.Column("empfehlung", sa.String(500), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("behoben_am", sa.DateTime()),
        sa.Column("occurrences", sa.Integer(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint("status IN ('offen','behoben','unterdrueckt')", name="ck_diag_finding_status"),
        sa.CheckConstraint("schweregrad IN ('info','warn','fail','kritisch')", name="ck_diag_finding_schwere"))
    for column in ("run_id", "check_id", "modul", "last_seen_at", "status"):
        op.create_index("ix_diag_finding_" + column, "diag_finding", [column])


def downgrade():
    op.drop_table("diag_finding")
    op.drop_table("diag_run")
