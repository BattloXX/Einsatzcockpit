"""Datenmodell des Infoscreen-Agents; Fachinhalte bleiben im Alarm-Infoscreen."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class InfoscreenDevice(TenantScoped, Base):
    __tablename__ = "infoscreen_device"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    device_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    hostname: Mapped[str | None] = mapped_column(String(255))
    os_version: Mapped[str | None] = mapped_column(String(100))
    agent_version: Mapped[str | None] = mapped_column(String(50))
    channel: Mapped[str] = mapped_column(String(16), default="stable", server_default="stable")
    update_pinned_version: Mapped[str | None] = mapped_column(String(50))
    update_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_ip: Mapped[str | None] = mapped_column(String(45))
    agent_connection_id: Mapped[str | None] = mapped_column(String(64))
    agent_worker_id: Mapped[str | None] = mapped_column(String(100))
    agent_presence_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    reported_config_version: Mapped[int | None] = mapped_column(BigInteger)
    config_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class InfoscreenSchedule(TenantScoped, Base):
    __tablename__ = "infoscreen_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Vienna", server_default="Europe/Vienna")
    ruhemodus: Mapped[str] = mapped_column(String(16), default="aus", server_default="aus")


class InfoscreenDisplay(TenantScoped, Base):
    __tablename__ = "infoscreen_display"
    __table_args__ = (UniqueConstraint("device_id", "display_key", name="uq_infoscreen_display_device_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("infoscreen_device.id", ondelete="CASCADE"), index=True)
    display_key: Mapped[str] = mapped_column(String(100))
    reported_status: Mapped[dict | None] = mapped_column(JSON)
    last_revision: Mapped[int | None] = mapped_column(BigInteger)
    edid_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    alarm_token_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("alarm_infoscreen_token.id", ondelete="SET NULL"),
        unique=True,
    )
    schedule_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("infoscreen_schedule.id", ondelete="SET NULL"),
        index=True,
    )
    power_mode: Mapped[str] = mapped_column(String(32), default="auto", server_default="auto")
    nachlauf_min: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    # NULL deaktiviert das Offline-Aufwecken fuer dieses Display.
    offline_wake_after_min: Mapped[int | None] = mapped_column(Integer, default=5, server_default="5")
    gsl_override_erlaubt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    resolution_hash: Mapped[str | None] = mapped_column(String(64))


class InfoscreenScheduleSlot(TenantScoped, Base):
    __tablename__ = "infoscreen_schedule_slot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("infoscreen_schedule.id", ondelete="CASCADE"), index=True
    )
    weekdays: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    power: Mapped[str] = mapped_column(String(8), default="on", server_default="on")
    rotation_json: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    label: Mapped[str | None] = mapped_column(String(200))


class InfoscreenScheduleException(TenantScoped, Base):
    __tablename__ = "infoscreen_schedule_exception"
    __table_args__ = (UniqueConstraint("schedule_id", "datum", name="uq_infoscreen_exception_schedule_datum"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("infoscreen_schedule.id", ondelete="CASCADE"), index=True
    )
    datum: Mapped[date] = mapped_column(Date)
    label: Mapped[str | None] = mapped_column(String(200))
    # NULL bzw. [] bedeutet ganzer Tag aus; sonst Ersatz-Slots als JSON-Liste.
    slots_ersatz: Mapped[list | None] = mapped_column(JSON)


class InfoscreenOverride(TenantScoped, Base):
    __tablename__ = "infoscreen_override"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("infoscreen_display.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    url_kind: Mapped[str] = mapped_column(String(32))
    url_ref: Mapped[str | None] = mapped_column(Text)
    von: Mapped[datetime] = mapped_column(DateTime)
    bis: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(String(500))
    aufgehoben_am: Mapped[datetime | None] = mapped_column(DateTime)
    aufgehoben_von: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))


class InfoscreenDeviceEvent(TenantScoped, Base):
    __tablename__ = "infoscreen_device_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("infoscreen_device.id", ondelete="CASCADE"), index=True)
    typ: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int | None] = mapped_column(BigInteger)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


class InfoscreenRelease(Base):
    __tablename__ = "infoscreen_release"
    __table_args__ = (
        UniqueConstraint("version", "channel", "arch", name="uq_infoscreen_release_version_channel_arch"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(50))
    channel: Mapped[str] = mapped_column(String(16), default="stable", server_default="stable")
    arch: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    min_version: Mapped[str | None] = mapped_column(String(50))
    rollout_percent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    storage_path: Mapped[str] = mapped_column(String(500))


class InfoscreenUrlAllowlist(TenantScoped, Base):
    __tablename__ = "infoscreen_url_allowlist"
    __table_args__ = (UniqueConstraint("org_id", "host", name="uq_infoscreen_allowlist_org_host"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host: Mapped[str] = mapped_column(String(253))
