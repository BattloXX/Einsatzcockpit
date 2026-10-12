"""Einsatzinfo-Mail: Empfaenger-Konfiguration und Versandprotokoll."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base

if TYPE_CHECKING:
    from app.models.master import AlarmType, Member
    from app.models.sms import SmsGroup
    from app.models.user import User


class MailEinsatzinfoRecipient(TenantScoped, Base):
    """Konfiguriert, wer bei einem Alarm eine Einsatzinfo-Mail erhaelt.

    alarm_type_id IS NULL bedeutet Basis-Verteiler. Genau eines von
    group_id / member_id / email ist gesetzt.
    """
    __tablename__ = "mail_einsatzinfo_recipient"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    alarm_type_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alarm_type.id", ondelete="CASCADE"), nullable=True, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sms_group.id", ondelete="CASCADE"), nullable=True
    )
    member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="CASCADE"), nullable=True
    )
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    alarm_type: Mapped[AlarmType | None] = relationship(lazy="joined")
    group: Mapped[SmsGroup | None] = relationship(lazy="joined")
    member: Mapped[Member | None] = relationship(lazy="joined")

    @property
    def is_global(self) -> bool:
        """True wenn fuer alle Stichworte gueltig (Basis-Verteiler)."""
        return self.alarm_type_id is None


class MailEinsatzinfoLog(TenantScoped, Base):
    """Protokolleintrag fuer einen Einsatzinfo-Mail-Versand."""
    __tablename__ = "mail_einsatzinfo_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="SET NULL"), nullable=True, index=True
    )
    alarm_type_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    triggered_by: Mapped[User | None] = relationship(
        "User", foreign_keys=[triggered_by_user_id], lazy="joined"
    )
    recipients: Mapped[list[MailEinsatzinfoLogRecipient]] = relationship(
        back_populates="mail_log", cascade="all, delete-orphan"
    )


class MailEinsatzinfoLogRecipient(Base):
    """Snapshot eines Empfaengers innerhalb eines Mail-Versandprotokolls."""
    __tablename__ = "mail_einsatzinfo_log_recipient"
    __table_args__ = (Index("ix_mail_einsatzinfo_log_recipient_log_id", "log_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    log_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mail_einsatzinfo_log.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Bcc-Block-Nummer
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    mail_log: Mapped[MailEinsatzinfoLog] = relationship(back_populates="recipients")
    member: Mapped[Member | None] = relationship(lazy="joined")
