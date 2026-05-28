from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

BOS_VALUES = ["Feuerwehr", "Rotes Kreuz", "Polizei", "Bauhof", "Privat"]


class FireDept(Base):
    """Organisation / Feuerwehr. Dient gleichzeitig als vollständige multi-org Entität."""
    __tablename__ = "fire_dept"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#b71921")
    bos: Mapped[str] = mapped_column(String(20), nullable=False, default="Feuerwehr")
    withdraw_press_factor: Mapped[float] = mapped_column(default=0.5)
    withdraw_press_reserve: Mapped[int] = mapped_column(Integer, default=10)

    # Multi-org fields
    is_home_org: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # IANA timezone (e.g. "Europe/Vienna"). NULL faellt auf settings.DEFAULT_TIMEZONE zurueck.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Fallback-Position für den Karten-Picker (wird genutzt, wenn Geocoding fehlschlägt)
    fallback_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    fallback_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    vehicles: Mapped[list[VehicleMaster]] = relationship(back_populates="dept")
    members: Mapped[list[Member]] = relationship(back_populates="org", foreign_keys="Member.org_id")
    settings: Mapped[OrgSettings | None] = relationship(back_populates="org", uselist=False)

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
    bos_override: Mapped[str | None] = mapped_column(String(20), nullable=True)

    dept: Mapped[FireDept] = relationship(back_populates="vehicles")

    @property
    def effective_bos(self) -> str:
        return self.bos_override or (self.dept.bos if self.dept else "Feuerwehr")


class Qualification(Base):
    __tablename__ = "qualification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    is_einsatzleiter: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_gruppenkommandant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Member(Base):
    __tablename__ = "member"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id: which organisation this member belongs to
    org_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fire_dept.id"), nullable=True)
    lastname: Mapped[str] = mapped_column(String(100), nullable=False)
    firstname: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    org: Mapped[FireDept | None] = relationship(back_populates="members", foreign_keys=[org_id])
    qualifications: Mapped[list[MemberQualification]] = relationship(
        back_populates="member", lazy="joined", passive_deletes=True,
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
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    member: Mapped[Member] = relationship(back_populates="qualifications")
    qualification: Mapped[Qualification] = relationship(lazy="joined")


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


class MessageSuggestion(Base):
    __tablename__ = "message_suggestion"

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
    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    primary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    footer_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    org: Mapped[FireDept] = relationship(back_populates="settings")


class SystemSettings(Base):
    """Systemweite Einstellungen als Key-Value-Store."""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)


class AlarmDispatchVehicle(Base):
    """Ausrückordnung: welche Fahrzeuge bei welchem Alarmtyp ausrücken (inkl. Reihenfolge)."""
    __tablename__ = "alarm_dispatch_vehicle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alarm_type_code: Mapped[str] = mapped_column(String(10), ForeignKey("alarm_type.code", ondelete="CASCADE"), nullable=False)
    vehicle_master_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("vehicle_master.id", ondelete="CASCADE"), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    alarm_type: Mapped[AlarmType] = relationship()
    vehicle: Mapped[VehicleMaster] = relationship()
