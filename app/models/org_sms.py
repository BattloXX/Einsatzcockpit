"""SMS-Provider-Konfiguration je Organisation."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OrgSmsConfig(Base):
    """Primaerer SMS-Provider und optionaler Fallback einer Organisation."""

    __tablename__ = "org_sms_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    primary_provider: Mapped[str] = mapped_column(String(20), nullable=False, default="gateway")
    fallback_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    eus_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    eus_base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    eus_auth_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="oauth")
    eus_client_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    eus_client_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    eus_timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    log_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    log_retention_max_count: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")

    @property
    def is_eus_fully_configured(self) -> bool:
        return bool(self.eus_base_url and self.eus_client_id and self.eus_client_secret_enc)
