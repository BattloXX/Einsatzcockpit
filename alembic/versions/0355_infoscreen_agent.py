"""Infoscreen-Agent: Geraete, Displays, Zeitplaene und Release-Grundlage."""

import sqlalchemy as sa

from alembic import op

revision = "0355"
down_revision = "0354"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "infoscreen_device",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("device_token_hash", sa.String(length=64), nullable=True),
        sa.Column("pairing_code_hash", sa.String(length=64), nullable=True),
        sa.Column("pairing_expires_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("os_version", sa.String(length=100), nullable=True),
        sa.Column("agent_version", sa.String(length=50), nullable=True),
        sa.Column("channel", sa.String(length=16), server_default="stable", nullable=False),
        sa.Column("update_pinned_version", sa.String(length=50), nullable=True),
        sa.Column("update_locked", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_ip", sa.String(length=45), nullable=True),
        sa.Column("agent_connection_id", sa.String(length=64), nullable=True),
        sa.Column("agent_worker_id", sa.String(length=100), nullable=True),
        sa.Column("agent_presence_expires_at", sa.DateTime(), nullable=True),
        sa.Column("config_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("erstellt_am", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_token_hash"),
        sa.UniqueConstraint("pairing_code_hash"),
    )
    op.create_index(op.f("ix_infoscreen_device_org_id"), "infoscreen_device", ["org_id"], unique=False)
    op.create_table(
        "infoscreen_release",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=16), server_default="stable", nullable=False),
        sa.Column("arch", sa.String(length=16), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("min_version", sa.String(length=50), nullable=True),
        sa.Column("rollout_percent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("blocked", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", "channel", "arch", name="uq_infoscreen_release_version_channel_arch"),
    )
    op.create_table(
        "infoscreen_schedule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="Europe/Vienna", nullable=False),
        sa.Column("ruhemodus", sa.String(length=16), server_default="aus", nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_infoscreen_schedule_org_id"), "infoscreen_schedule", ["org_id"], unique=False)
    op.create_table(
        "infoscreen_display",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("display_key", sa.String(length=100), nullable=False),
        sa.Column("edid_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("alarm_token_id", sa.BigInteger(), nullable=True),
        sa.Column("schedule_id", sa.Integer(), nullable=True),
        sa.Column("power_mode", sa.String(length=32), server_default="auto", nullable=False),
        sa.Column("nachlauf_min", sa.Integer(), server_default="10", nullable=False),
        sa.Column("offline_wake_after_min", sa.Integer(), server_default="5", nullable=True),
        sa.Column("gsl_override_erlaubt", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("aktiv", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["alarm_token_id"], ["alarm_infoscreen_token.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["infoscreen_device.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["schedule_id"], ["infoscreen_schedule.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alarm_token_id"),
        sa.UniqueConstraint("device_id", "display_key", name="uq_infoscreen_display_device_key"),
    )
    op.create_index(op.f("ix_infoscreen_display_device_id"), "infoscreen_display", ["device_id"], unique=False)
    op.create_index(op.f("ix_infoscreen_display_org_id"), "infoscreen_display", ["org_id"], unique=False)
    op.create_index(op.f("ix_infoscreen_display_schedule_id"), "infoscreen_display", ["schedule_id"], unique=False)
    op.create_table(
        "infoscreen_schedule_slot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("weekdays", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("power", sa.String(length=8), server_default="on", nullable=False),
        sa.Column("rotation_json", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["schedule_id"], ["infoscreen_schedule.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_infoscreen_schedule_slot_org_id"), "infoscreen_schedule_slot", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_infoscreen_schedule_slot_schedule_id"), "infoscreen_schedule_slot", ["schedule_id"], unique=False
    )
    op.create_table(
        "infoscreen_schedule_exception",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("datum", sa.Date(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("slots_ersatz", sa.JSON(), nullable=True),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["schedule_id"], ["infoscreen_schedule.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_id", "datum", name="uq_infoscreen_exception_schedule_datum"),
    )
    op.create_index(
        op.f("ix_infoscreen_schedule_exception_org_id"), "infoscreen_schedule_exception", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_infoscreen_schedule_exception_schedule_id"),
        "infoscreen_schedule_exception",
        ["schedule_id"],
        unique=False,
    )
    op.create_table(
        "infoscreen_override",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("display_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("url_kind", sa.String(length=32), nullable=False),
        sa.Column("url_ref", sa.Text(), nullable=True),
        sa.Column("von", sa.DateTime(), nullable=False),
        sa.Column("bis", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("aufgehoben_am", sa.DateTime(), nullable=True),
        sa.Column("aufgehoben_von", sa.BigInteger(), nullable=True),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["aufgehoben_von"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["display_id"], ["infoscreen_display.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_infoscreen_override_display_id"), "infoscreen_override", ["display_id"], unique=False)
    op.create_index(op.f("ix_infoscreen_override_org_id"), "infoscreen_override", ["org_id"], unique=False)
    op.create_table(
        "infoscreen_device_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("typ", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["infoscreen_device.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_infoscreen_device_event_device_id"), "infoscreen_device_event", ["device_id"], unique=False
    )
    op.create_index(op.f("ix_infoscreen_device_event_org_id"), "infoscreen_device_event", ["org_id"], unique=False)
    op.create_index(op.f("ix_infoscreen_device_event_ts"), "infoscreen_device_event", ["ts"], unique=False)
    op.create_table(
        "infoscreen_url_allowlist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["fire_dept.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "host", name="uq_infoscreen_allowlist_org_host"),
    )
    op.create_index(op.f("ix_infoscreen_url_allowlist_org_id"), "infoscreen_url_allowlist", ["org_id"], unique=False)


def downgrade() -> None:
    op.drop_table("infoscreen_url_allowlist")
    op.drop_table("infoscreen_device_event")
    op.drop_table("infoscreen_override")
    op.drop_table("infoscreen_schedule_exception")
    op.drop_table("infoscreen_schedule_slot")
    op.drop_table("infoscreen_display")
    op.drop_table("infoscreen_schedule")
    op.drop_table("infoscreen_release")
    op.drop_table("infoscreen_device")
