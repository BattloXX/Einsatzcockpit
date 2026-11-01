"""Geraetegebundene ActivityKit-Push-Tokens."""
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.user import DeviceToken, User


class LiveActivityToken(Base):
    __tablename__ = "live_activity_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    device_token_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("device_token.id", ondelete="SET NULL"), nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    token: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_error: Mapped[str | None] = mapped_column(String(120), nullable=True)

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    device_token: Mapped[DeviceToken | None] = relationship("DeviceToken", foreign_keys=[device_token_id])
