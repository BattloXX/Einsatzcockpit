"""SMS-Gruppen, Einsatzinfo-Empfaenger, SMS-Log und SMS-Empfang.

Erweiterungen:
  1. Einsatzinfo-SMS bei Alarm (automatisch, konfigurierbar je Stichwort)
  2. Manueller SMS-Versand an Gruppen/Mitglieder
  3. SMS-Empfang (SmsInbox) + Weiterleitungsregeln nach Absendernummer
     (Teams-Webhook und/oder SMS an Gruppen/Mitglieder/Ad-hoc-Nummern)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base

if TYPE_CHECKING:
    from app.models.master import AlarmType, Member
    from app.models.user import User


class SmsGroup(TenantScoped, Base):
    """Benannte Gruppe von Mitgliedern fuer den SMS-Versand."""
    __tablename__ = "sms_group"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    members: Mapped[list[SmsGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan", lazy="joined"
    )


class SmsGroupMember(Base):
    """Assoziation: Mitglied ist Mitglied einer SMS-Gruppe."""
    __tablename__ = "sms_group_member"

    sms_group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sms_group.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="CASCADE"), primary_key=True
    )

    group: Mapped[SmsGroup] = relationship(back_populates="members")
    member: Mapped[Member] = relationship(lazy="joined")


class SmsEinsatzinfoRecipient(TenantScoped, Base):
    """Konfiguriert, wer bei einem Alarm eine Einsatzinfo-SMS erhaelt.

    alarm_type_id IS NULL  → gilt fuer alle Stichworte (Basis-Verteiler)
    alarm_type_id IS NOT NULL → gilt nur fuer dieses Stichwort

    Genau eines von group_id / member_id ist gesetzt.
    """
    __tablename__ = "sms_einsatzinfo_recipient"

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

    alarm_type: Mapped[AlarmType | None] = relationship(lazy="joined")
    group: Mapped[SmsGroup | None] = relationship(lazy="joined")
    member: Mapped[Member | None] = relationship(lazy="joined")

    @property
    def is_global(self) -> bool:
        """True wenn fuer alle Stichworte gueltig (Basis-Verteiler)."""
        return self.alarm_type_id is None


class SmsLog(TenantScoped, Base):
    """Protokolleintrag fuer jeden SMS-Versand (automatisch oder manuell)."""
    __tablename__ = "sms_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), index=True)
    # "alarm" = automatischer Einsatzinfo-Versand, "manual" = manueller Versand
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    alarm_type_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    triggered_by: Mapped[User | None] = relationship(
        "User", foreign_keys=[triggered_by_user_id], lazy="joined"
    )


class SmsForwardRule(TenantScoped, Base):
    """Verarbeitungsregel fuer empfangene SMS: matcht auf Absendernummer,
    leitet an Teams-Webhook und/oder SMS-Ziele (Gruppen/Mitglieder/Ad-hoc) weiter.
    """
    __tablename__ = "sms_forward_rule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # "exact" = Nummer muss exakt uebereinstimmen, "prefix" = Nummer beginnt mit match_number
    match_type: Mapped[str] = mapped_column(String(10), nullable=False, default="exact")
    match_number: Mapped[str] = mapped_column(String(30), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    forward_teams: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NULL -> OrgSettings.sms_receive_teams_webhook_url wird verwendet
    teams_webhook_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # kommagetrennte Ad-hoc-Rufnummern
    forward_adhoc_numbers: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Absendernummer im weitergeleiteten SMS-Text voranstellen
    prepend_sender: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    groups: Mapped[list[SmsForwardRuleGroup]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", lazy="joined"
    )
    members: Mapped[list[SmsForwardRuleMember]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", lazy="joined"
    )

    @property
    def has_sms_targets(self) -> bool:
        return bool(self.groups or self.members or (self.forward_adhoc_numbers or "").strip())


class SmsForwardRuleGroup(Base):
    """Assoziation: Weiterleitungsregel -> SMS-Gruppe."""
    __tablename__ = "sms_forward_rule_group"

    rule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sms_forward_rule.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sms_group.id", ondelete="CASCADE"), primary_key=True
    )

    rule: Mapped[SmsForwardRule] = relationship(back_populates="groups")
    group: Mapped[SmsGroup] = relationship(lazy="joined")


class SmsForwardRuleMember(Base):
    """Assoziation: Weiterleitungsregel -> einzelnes Mitglied."""
    __tablename__ = "sms_forward_rule_member"

    rule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sms_forward_rule.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="CASCADE"), primary_key=True
    )

    rule: Mapped[SmsForwardRule] = relationship(back_populates="members")
    member: Mapped[Member] = relationship(lazy="joined")


class SmsInbox(TenantScoped, Base):
    """Protokolleintrag fuer jede ueber das Gateway empfangene SMS."""
    __tablename__ = "sms_inbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    received_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), index=True
    )
    from_number: Mapped[str] = mapped_column(String(30), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    gateway_token_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sms_gateway_token.id", ondelete="SET NULL"), nullable=True
    )
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    matched_rule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sms_forward_rule.id", ondelete="SET NULL"), nullable=True
    )
    # Kurzbeschreibung des Ergebnisses, z.B. "Teams ✓, 3 SMS" oder "keine Regel getroffen"
    forward_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)

    matched_rule: Mapped[SmsForwardRule | None] = relationship(lazy="joined")
