"""Persistente Versandauftraege der externen Nachrichten-API."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ApiMessage(TenantScoped, Base):
    __tablename__ = "api_message"
    __table_args__ = (
        UniqueConstraint("org_id", "kanal", "external_key", name="uq_api_message_org_kanal_key"),
        Index("ix_api_message_org_status_id", "org_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    api_key_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("api_key.id"), nullable=False)
    external_key: Mapped[str] = mapped_column(String(200), nullable=False)
    kanal: Mapped[str] = mapped_column(String(10), nullable=False)
    betreff: Mapped[str | None] = mapped_column(String(500))
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text)
    sms_log_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sms_log.id", ondelete="SET NULL")
    )
    recipients: Mapped[list[ApiMessageRecipient]] = relationship(
        back_populates="message", cascade="all, delete-orphan", order_by="ApiMessageRecipient.id"
    )


class ApiMessageRecipient(TenantScoped, Base):
    __tablename__ = "api_message_recipient"
    __table_args__ = (
        Index("ix_api_message_recipient_message_id", "message_id"),
        Index("ix_api_message_recipient_dispatch", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("api_message.id", ondelete="CASCADE"), nullable=False
    )
    ziel: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300))
    member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    provider: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    message: Mapped[ApiMessage] = relationship(back_populates="recipients")
