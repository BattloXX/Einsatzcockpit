"""Persistenter Zustand und Protokoll der Dienstueberwachung."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base

DIENST_LABELS = {
    "print_gateway": "Print-Gateway",
    "sms_gateway": "SMS-Gateway",
    "alarm_seriell": "Alarm seriell (W&T)",
    "alarm_dibos": "Alarm DIBOS",
}


class DienstStatus(TenantScoped, Base):
    __tablename__ = "dienst_status"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_dienst_status_org_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    since: Mapped[datetime | None] = mapped_column(DateTime)
    down_since: Mapped[datetime | None] = mapped_column(DateTime)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))
    fail_cycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ok_cycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_probe_ok: Mapped[bool | None]
    last_probe_error: Mapped[str | None] = mapped_column(String(500))
    outage_notified_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_repeat_at: Mapped[datetime | None] = mapped_column(DateTime)


class DienstMonitorLog(TenantScoped, Base):
    __tablename__ = "dienst_monitor_log"
    __table_args__ = (Index("ix_dienst_monitor_log_org_gesendet", "org_id", "gesendet_am"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    kanal: Mapped[str] = mapped_column(String(20), nullable=False)
    empfaenger: Mapped[str | None] = mapped_column(String(255))
    betreff: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    fehlertext: Mapped[str | None] = mapped_column(String(500))
    payload_excerpt: Mapped[str | None] = mapped_column(String(1000))
    gesendet_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class DienstMonitorToken(Base):
    __tablename__ = "dienst_monitor_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
