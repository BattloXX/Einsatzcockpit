"""Systemweite Regeln fuer den sicheren Empfangs-Mail-Relay."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MailWeiterleitung(Base):
    __tablename__ = "mail_weiterleitung"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adresse: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    ziele: Mapped[str] = mapped_column(Text, nullable=False)
    modus: Mapped[str] = mapped_column(String(10), nullable=False, default="inline")
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    beschreibung: Mapped[str | None] = mapped_column(String(255))
    max_pro_stunde: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    zaehler: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    letzte_weiterleitung_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class MailWeiterleitungLog(Base):
    __tablename__ = "mail_weiterleitung_log"
    __table_args__ = (Index("ix_mail_weiterleitung_log_retry", "status", "updated_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    weiterleitung_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mail_weiterleitung.id", ondelete="CASCADE"), nullable=False
    )
    zeit: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    absenderdomain: Mapped[str | None] = mapped_column(String(253))
    betreff: Mapped[str | None] = mapped_column(String(255))
    fehlercode: Mapped[str | None] = mapped_column(String(64))
    versuche: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resend_email_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
