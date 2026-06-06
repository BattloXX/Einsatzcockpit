"""Großschadenslage: alle neuen Lage-Tabellen + AlarmType/OrgSettings-Felder

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-06 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Bestehende Tabellen erweitern ────────────────────────────────────────
    op.add_column("alarm_type",
        sa.Column("triggers_major_incident", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("org_settings",
        sa.Column("mi_auto_adopt", sa.Boolean(), nullable=False, server_default="1"))

    # ── major_incident ───────────────────────────────────────────────────────
    op.create_table(
        "major_incident",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("fire_dept.id"), nullable=False, index=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("is_exercise", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("auto_adopt", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("public_token", sa.String(64), nullable=True, unique=True),
        sa.Column("public_token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("started_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_major_incident_public_token", "major_incident", ["public_token"])

    # ── site_sector ──────────────────────────────────────────────────────────
    op.create_table(
        "site_sector",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("major_incident_id", sa.Integer(),
                  sa.ForeignKey("major_incident.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("leader_label", sa.String(80), nullable=True),
        sa.Column("color", sa.String(7), nullable=True),
    )

    # ── staff_assignment ─────────────────────────────────────────────────────
    op.create_table(
        "staff_assignment",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("major_incident_id", sa.Integer(),
                  sa.ForeignKey("major_incident.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("function", sa.String(20), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("member.id"), nullable=True),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
    )

    # ── incident_site ────────────────────────────────────────────────────────
    op.create_table(
        "incident_site",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("major_incident_id", sa.Integer(),
                  sa.ForeignKey("major_incident.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("fire_dept.id"),
                  nullable=False, index=True),
        sa.Column("sector_id", sa.Integer(),
                  sa.ForeignKey("site_sector.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bezeichnung", sa.String(160), nullable=False),
        sa.Column("einsatzgrund", sa.String(160), nullable=True),
        sa.Column("ort", sa.String(120), nullable=True),
        sa.Column("strasse", sa.String(120), nullable=True),
        sa.Column("hausnr", sa.String(20), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("source", sa.String(12), nullable=False, server_default="manual"),
        sa.Column("external_key", sa.String(64), nullable=True, index=True),
        sa.Column("alarm_stufe", sa.String(8), nullable=True),
        sa.Column("phase", sa.String(20), nullable=False, server_default="eingegangen",
                  index=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("danger_score", sa.Integer(), nullable=True),
        sa.Column("urgency_score", sa.Integer(), nullable=True),
        sa.Column("sort_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("incident_id", sa.Integer(),
                  sa.ForeignKey("incident.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
    )

    # ── site_resource_assignment ─────────────────────────────────────────────
    op.create_table(
        "site_resource_assignment",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_site_id", sa.Integer(),
                  sa.ForeignKey("incident_site.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("resource_type", sa.String(12), nullable=False),
        sa.Column("vehicle_id", sa.Integer(),
                  sa.ForeignKey("vehicle_master.id", ondelete="SET NULL"), nullable=True),
        sa.Column("member_id", sa.Integer(),
                  sa.ForeignKey("member.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
    )

    # ── site_log_entry ───────────────────────────────────────────────────────
    op.create_table(
        "site_log_entry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_site_id", sa.Integer(),
                  sa.ForeignKey("incident_site.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("author_name", sa.String(120), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="note"),
        sa.Column("text", sa.Text(), nullable=False),
    )

    # ── site_media ───────────────────────────────────────────────────────────
    op.create_table(
        "site_media",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_site_id", sa.Integer(),
                  sa.ForeignKey("incident_site.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("stored_filename", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(12), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("author_name", sa.String(120), nullable=True),
    )

    # ── comm_log_entry ───────────────────────────────────────────────────────
    op.create_table(
        "comm_log_entry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("major_incident_id", sa.Integer(),
                  sa.ForeignKey("major_incident.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("related_site_id", sa.Integer(),
                  sa.ForeignKey("incident_site.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("direction", sa.String(4), nullable=False),
        sa.Column("channel", sa.String(40), nullable=True),
        sa.Column("partner", sa.String(120), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_request", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("handled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("author_name", sa.String(120), nullable=True),
    )

    # ── citizen_report ───────────────────────────────────────────────────────
    op.create_table(
        "citizen_report",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("major_incident_id", sa.Integer(),
                  sa.ForeignKey("major_incident.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("reporter_name", sa.String(120), nullable=True),
        sa.Column("reporter_contact", sa.String(120), nullable=True),
        sa.Column("ort", sa.String(120), nullable=True),
        sa.Column("strasse", sa.String(160), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("photo_filename", sa.String(64), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_ip", sa.String(45), nullable=True),
        sa.Column("site_id", sa.Integer(),
                  sa.ForeignKey("incident_site.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("citizen_report")
    op.drop_table("comm_log_entry")
    op.drop_table("site_media")
    op.drop_table("site_log_entry")
    op.drop_table("site_resource_assignment")
    op.drop_table("incident_site")
    op.drop_table("staff_assignment")
    op.drop_table("site_sector")
    op.drop_index("ix_major_incident_public_token", "major_incident")
    op.drop_table("major_incident")
    op.drop_column("org_settings", "mi_auto_adopt")
    op.drop_column("alarm_type", "triggers_major_incident")
