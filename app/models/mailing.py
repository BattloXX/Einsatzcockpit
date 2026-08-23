"""Datenmodell des optionalen Mailing-Moduls."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base
from app.models.master import MemberTag


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MailingTemplate(TenantScoped, Base):
    __tablename__ = "mailing_template"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    preheader: Mapped[str | None] = mapped_column(String(500))
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class MailingRecipientList(TenantScoped, Base):
    __tablename__ = "mailing_recipient_list"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="static")
    auto_source: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    filter_json: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    entries: Mapped[list[MailingRecipientListEntry]] = relationship(
        back_populates="recipient_list", cascade="all, delete-orphan"
    )


class MailingRecipientListEntry(TenantScoped, Base):
    __tablename__ = "mailing_recipient_list_entry"
    __table_args__ = (UniqueConstraint("org_id", "list_id", "email", name="uq_mailing_list_email"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    list_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mailing_recipient_list.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    recipient_list: Mapped[MailingRecipientList] = relationship(back_populates="entries")
    tags: Mapped[list[MemberTag]] = relationship(secondary="mailing_recipient_list_entry_tag")


class MailingRecipientListEntryTag(Base):
    __tablename__ = "mailing_recipient_list_entry_tag"
    entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("mailing_recipient_list_entry.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("member_tag.id", ondelete="CASCADE"),
        primary_key=True,
    )


class MailingCampaign(TenantScoped, Base):
    __tablename__ = "mailing_campaign"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mailing_template.id"), nullable=False)
    recipient_list_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("mailing_recipient_list.id"))
    source_incident_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="SET NULL"))
    subject_override: Mapped[str | None] = mapped_column(String(500))
    reply_to_override: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suppressed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    track_opens: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    track_clicks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_attempts_override: Mapped[int | None] = mapped_column(Integer)
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    template: Mapped[MailingTemplate] = relationship()
    recipient_list: Mapped[MailingRecipientList] = relationship()
    queue_items: Mapped[list[MailingQueueItem]] = relationship(back_populates="campaign", cascade="all, delete-orphan")
    attachments: Mapped[list[MailingCampaignAttachment]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    recipient_list_links: Mapped[list[MailingCampaignRecipientList]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class MailingQueueItem(TenantScoped, Base):
    __tablename__ = "mailing_queue_item"
    __table_args__ = (Index("ix_mailing_queue_dispatch", "org_id", "status", "next_attempt_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mailing_campaign.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    resend_message_id: Mapped[str | None] = mapped_column(String(200))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_clicked_at: Mapped[datetime | None] = mapped_column(DateTime)
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    campaign: Mapped[MailingCampaign] = relationship(back_populates="queue_items")


class MailingCampaignAttachment(TenantScoped, Base):
    __tablename__ = "mailing_campaign_attachment"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mailing_campaign.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    campaign: Mapped[MailingCampaign] = relationship(back_populates="attachments")


class MailingLinkClick(TenantScoped, Base):
    __tablename__ = "mailing_link_click"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    queue_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mailing_queue_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    clicked_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))


class MailingCampaignRecipientList(Base):
    __tablename__ = "mailing_campaign_recipient_list"
    campaign_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mailing_campaign.id", ondelete="CASCADE"), primary_key=True
    )
    recipient_list_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mailing_recipient_list.id", ondelete="CASCADE"), primary_key=True
    )
    campaign: Mapped[MailingCampaign] = relationship(back_populates="recipient_list_links")
    recipient_list: Mapped[MailingRecipientList] = relationship()


class MailingConfig(Base):
    __tablename__ = "mailing_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resend_api_key_enc: Mapped[str | None] = mapped_column(Text)
    resend_webhook_secret_enc: Mapped[str | None] = mapped_column(Text)
    from_addr: Mapped[str | None] = mapped_column(String(320))
    reply_to_default: Mapped[str | None] = mapped_column(String(320))
    sender_display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    @property
    def is_fully_configured(self) -> bool:
        return bool(self.enabled and self.resend_api_key_enc and self.from_addr)


class MailingWebhookEvent(TenantScoped, Base):
    __tablename__ = "mailing_webhook_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    queue_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("mailing_queue_item.id", ondelete="SET NULL"), index=True
    )
    campaign_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("mailing_campaign.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resend_message_id: Mapped[str | None] = mapped_column(String(200), index=True)
    svix_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text)


class MailingSuppressionEntry(TenantScoped, Base):
    __tablename__ = "mailing_suppression_entry"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_mailing_suppression_email"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    source_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("mailing_webhook_event.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class MailingApiImportBatch(Base):
    __tablename__ = "mailing_api_import_batch"
    __table_args__ = (UniqueConstraint("org_id", "list_id", "external_key", name="uq_mailing_api_import_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False, index=True
    )
    list_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mailing_recipient_list.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_key: Mapped[str] = mapped_column(String(200), nullable=False)
    added_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
