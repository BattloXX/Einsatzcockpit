"""Kurzlebige SMS-Verifizierungen fuer das Buergermeldeportal."""
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base


class CitizenReportVerification(TenantScoped, Base):
    __tablename__ = "citizen_report_verification"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    major_incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), nullable=False
    )
    pin_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str] = mapped_column(String(120), nullable=False)
    form_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False
    )
