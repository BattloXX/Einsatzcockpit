"""Datenmodell fuer Windows-Druckagents.

Revision ID: 0289
Revises: 0288
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0289"
down_revision = "0288"
branch_labels = None
depends_on = None


def _tabellen(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _spalten(bind, tabelle: str) -> set[str]:
    if tabelle not in _tabellen(bind):
        return set()
    return {spalte["name"] for spalte in sa.inspect(bind).get_columns(tabelle)}


def _indizes(bind, tabelle: str) -> set[str]:
    if tabelle not in _tabellen(bind):
        return set()
    return {index["name"] for index in sa.inspect(bind).get_indexes(tabelle) if index.get("name")}


def _fremdschluessel(bind, tabelle: str) -> set[str]:
    if tabelle not in _tabellen(bind):
        return set()
    return {
        schluessel["name"] for schluessel in sa.inspect(bind).get_foreign_keys(tabelle)
        if schluessel.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()

    gateway_spalten = _spalten(bind, "gateway")
    if "kind" not in gateway_spalten or "agent_info" not in gateway_spalten:
        with op.batch_alter_table("gateway") as batch:
            if "kind" not in gateway_spalten:
                batch.add_column(sa.Column(
                    "kind", sa.String(20), nullable=False, server_default="linux"
                ))
            if "agent_info" not in gateway_spalten:
                batch.add_column(sa.Column("agent_info", sa.JSON(), nullable=True))

    print_job_spalten = _spalten(bind, "print_job")
    print_job_fks = _fremdschluessel(bind, "print_job")
    neue_print_job_spalten = (
        sa.Column("delivery", sa.String(10), nullable=False, server_default="push"),
        sa.Column("lease_gateway_id", sa.BigInteger(), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase", sa.String(20), nullable=True),
        sa.Column("spool_ref", sa.String(120), nullable=True),
        sa.Column("queued_at", sa.DateTime(), nullable=True),
        sa.Column("agent_contact_at", sa.DateTime(), nullable=True),
    )
    if any(spalte.name not in print_job_spalten for spalte in neue_print_job_spalten) or (
        "fk_print_job_lease_gateway" not in print_job_fks
    ):
        with op.batch_alter_table("print_job") as batch:
            for spalte in neue_print_job_spalten:
                if spalte.name not in print_job_spalten:
                    batch.add_column(spalte)
            if "fk_print_job_lease_gateway" not in print_job_fks:
                batch.create_foreign_key(
                    "fk_print_job_lease_gateway", "gateway", ["lease_gateway_id"], ["id"],
                    ondelete="SET NULL",
                )

    if "ix_print_job_delivery_status" not in _indizes(bind, "print_job"):
        op.create_index("ix_print_job_delivery_status", "print_job", ["delivery", "status"])

    if "printer_agent_route" not in _tabellen(bind):
        op.create_table(
            "printer_agent_route",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "org_id", sa.BigInteger(),
                sa.ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column(
                "printer_id", sa.BigInteger(),
                sa.ForeignKey("printer.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "gateway_id", sa.BigInteger(),
                sa.ForeignKey("gateway.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("local_queue", sa.String(255), nullable=False),
            sa.Column("render_mode", sa.String(20), nullable=False, server_default="driver"),
            sa.Column("options", sa.JSON(), nullable=True),
            sa.Column("identity_check", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("aktiv", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("identity", sa.JSON(), nullable=True),
            sa.Column("ready", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("readiness", sa.JSON(), nullable=True),
            sa.Column("erstellt_am", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("printer_id", "gateway_id", name="uq_printer_agent_route"),
        )
    if "ix_printer_agent_route_org_gateway" not in _indizes(bind, "printer_agent_route"):
        op.create_index(
            "ix_printer_agent_route_org_gateway", "printer_agent_route", ["org_id", "gateway_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()

    if "printer_agent_route" in _tabellen(bind):
        op.drop_table("printer_agent_route")

    if "ix_print_job_delivery_status" in _indizes(bind, "print_job"):
        op.drop_index("ix_print_job_delivery_status", table_name="print_job")

    print_job_spalten = _spalten(bind, "print_job")
    print_job_fks = _fremdschluessel(bind, "print_job")
    loeschen = (
        "delivery", "lease_gateway_id", "lease_token", "lease_expires_at", "lease_generation",
        "phase", "spool_ref", "queued_at", "agent_contact_at",
    )
    if any(spalte in print_job_spalten for spalte in loeschen):
        with op.batch_alter_table("print_job") as batch:
            if "fk_print_job_lease_gateway" in print_job_fks:
                batch.drop_constraint("fk_print_job_lease_gateway", type_="foreignkey")
            for spalte in loeschen:
                if spalte in print_job_spalten:
                    batch.drop_column(spalte)

    gateway_spalten = _spalten(bind, "gateway")
    if "kind" in gateway_spalten or "agent_info" in gateway_spalten:
        with op.batch_alter_table("gateway") as batch:
            if "agent_info" in gateway_spalten:
                batch.drop_column("agent_info")
            if "kind" in gateway_spalten:
                batch.drop_column("kind")
