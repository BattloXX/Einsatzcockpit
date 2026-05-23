from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FireDept(Base):
    """Organisation / Feuerwehr. Dient gleichzeitig als vollständige multi-org Entität."""
    __tablename__ = "fire_dept"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#b71921")
    withdraw_press_factor: Mapped[float] = mapped_column(default=0.5)
    withdraw_press_reserve: Mapped[int] = mapped_column(Integer, default=10)

    # Multi-org fields
    is_home_org: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    logo_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    street: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    vehicles: Mapped[List["VehicleMaster"]] = relationship(back_populates="dept")
    members: Mapped[List["Member"]] = relationship(back_populates="org", foreign_keys="Member.org_id")
    settings: Mapped[Optional["OrgSettings"]] = relationship(back_populates="org", uselist=False)

    @property
    def display_name(self) -> str:
        return self.name


class VehicleMaster(Base):
    __tablename__ = "vehicle_master"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dept_id: Mapped[int] = mapped_column(Integer, ForeignKey("fire_dept.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    type: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_first_train: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    dept: Mapped["FireDept"] = relationship(back_populates="vehicles")


class Qualification(Base):
    __tablename__ = "qualification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)


class Member(Base):
    __tablename__ = "member"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id: which organisation this member belongs to
    org_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("fire_dept.id"), nullable=True)
    lastname: Mapped[str] = mapped_column(String(100), nullable=False)
    firstname: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    org: Mapped[Optional["FireDept"]] = relationship(back_populates="members", foreign_keys=[org_id])
    qualifications: Mapped[List["MemberQualification"]] = relationship(
        back_populates="member", lazy="joined"
    )

    @property
    def full_name(self) -> str:
        return f"{self.firstname} {self.lastname}"

    @property
    def is_agt(self) -> bool:
        return any(mq.qualification.code == "AGT" for mq in self.qualifications if mq.qualification)


class MemberQualification(Base):
    __tablename__ = "member_qualification"

    member_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="CASCADE"), primary_key=True)
    qualification_id: Mapped[int] = mapped_column(Integer, ForeignKey("qualification.id", ondelete="CASCADE"), primary_key=True)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    member: Mapped["Member"] = relationship(back_populates="qualifications")
    qualification: Mapped["Qualification"] = relationship(lazy="joined")


class AlarmType(Base):
    __tablename__ = "alarm_type"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="T")
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    default_first_train_only: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_neighbors: Mapped[bool] = mapped_column(Boolean, default=False)


class TaskSuggestion(Base):
    __tablename__ = "task_suggestion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alarm_type_code: Mapped[str] = mapped_column(String(10), ForeignKey("alarm_type.code"), nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)


class LageHint(Base):
    __tablename__ = "lage_hint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)


class DefaultMessage(Base):
    __tablename__ = "default_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alarm_type_code: Mapped[str] = mapped_column(String(10), ForeignKey("alarm_type.code"), nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    due_after_sec: Mapped[int] = mapped_column(Integer, default=300)


class OrgSettings(Base):
    """Organisations-spezifische Einstellungen (Logo, Farbe, etc.)."""
    __tablename__ = "org_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False)
    logo_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    primary_color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    footer_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    org: Mapped["FireDept"] = relationship(back_populates="settings")


class SystemSettings(Base):
    """Systemweite Einstellungen als Key-Value-Store."""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)


class AlarmDispatchVehicle(Base):
    """Ausrückordnung: welche Fahrzeuge bei welchem Alarmtyp ausrücken (inkl. Reihenfolge)."""
    __tablename__ = "alarm_dispatch_vehicle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alarm_type_code: Mapped[str] = mapped_column(String(10), ForeignKey("alarm_type.code", ondelete="CASCADE"), nullable=False)
    vehicle_master_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("vehicle_master.id", ondelete="CASCADE"), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    alarm_type: Mapped["AlarmType"] = relationship()
    vehicle: Mapped["VehicleMaster"] = relationship()
