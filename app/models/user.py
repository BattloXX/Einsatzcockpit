from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.master import FireDept, VehicleMaster
    from app.models.password_reset import PasswordResetToken


class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)

    users: Mapped[list[UserRole]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_device: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # org_id: which organisation this user belongs to (NULL = system_admin without org)
    org_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fire_dept.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Lockout (Phase 7)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user_roles: Mapped[list[UserRole]] = relationship(back_populates="user", lazy="joined")
    push_subscriptions: Mapped[list[PushSubscription]] = relationship(back_populates="user")
    password_reset_tokens: Mapped[list[PasswordResetToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    org: Mapped[FireDept | None] = relationship(
        "FireDept", foreign_keys=[org_id], lazy="joined"
    )

    @property
    def roles(self) -> list[Role]:
        return [ur.role for ur in self.user_roles if ur.role is not None]

    @property
    def role_codes(self) -> set[str]:
        return {r.code for r in self.roles}

    @property
    def is_system_admin(self) -> bool:
        return "system_admin" in self.role_codes

    @property
    def is_org_admin(self) -> bool:
        return bool(self.role_codes & {"system_admin", "admin", "org_admin"})


class UserRole(Base):
    __tablename__ = "user_role"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True)

    user: Mapped[User] = relationship(back_populates="user_roles")
    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")


class ApiKey(Base):
    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="RESTRICT"), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_active(self) -> bool:
        if self.revoked_at:
            return False
        if self.expires_at and self.expires_at < datetime.now(UTC):
            return False
        return True


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    api_key_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("api_key.id"), nullable=True)
    incident_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class PushSubscription(Base):
    __tablename__ = "push_subscription"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    user: Mapped[User] = relationship(back_populates="push_subscriptions")


class DeviceToken(Base):
    __tablename__ = "device_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    vehicle_master_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("vehicle_master.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Standort-Tracking (A2)
    last_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_location_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duty_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    vehicle: Mapped[VehicleMaster | None] = relationship("VehicleMaster", foreign_keys=[vehicle_master_id])

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class FcmToken(Base):
    """FCM Registration Token für native Android-App Push-Benachrichtigungen."""
    __tablename__ = "fcm_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    device_token_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("device_token.id", ondelete="SET NULL"), nullable=True
    )
    token: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="android")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    device_token: Mapped[DeviceToken | None] = relationship("DeviceToken", foreign_keys=[device_token_id])


class PushLog(Base):
    __tablename__ = "push_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="system")
    target_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)

    target_user: Mapped[User | None] = relationship("User", foreign_keys=[target_user_id])


class SmsGatewayToken(Base):
    """Connection-Token für den SMS-Gateway-Docker-Container oder die Android-App."""
    __tablename__ = "sms_gateway_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    org: Mapped[FireDept | None] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
