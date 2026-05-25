from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Fixed column codes (always present)
FIXED_COLUMNS = ["dispatched", "active", "tasks", "messages", "neighbor", "rescued"]
UNIT_STATUS_VALUES = [
    "Einsatz übernommen",
    "Am Einsatzort",
    "Einsatzbereit",
]
TRAFFIC_LIGHT_VALUES = ["open", "in_progress", "done", "cancelled"]
PERSON_STATUS_VALUES = ["gefunden", "versorgt", "abtransportiert", "verstorben"]
FIXED_COLUMN_TITLES = {
    "dispatched": "Disponierte Fahrzeuge",
    "active":     "Tatsächlich im Einsatz",
    "tasks":      "Aufträge",
    "messages":   "Meldungen",
    "neighbor":   "Nachalarmierung",
    "rescued":    "Gerettete Personen",
}


class Incident(Base):
    __tablename__ = "incident"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    external_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    nummer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alarm_type_code: Mapped[str] = mapped_column(String(10), nullable=False, default="T1")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    incident_leader_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    incident_leader_member_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("member.id"), nullable=True)
    # primary_org_id: the organisation leading this incident
    primary_org_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fire_dept.id"), nullable=True)
    is_exercise: Mapped[bool] = mapped_column(Boolean, default=False)
    address_street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    report_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    # 48h-Auto-Close-Lifecycle: Warnung versandt + Anzahl der "Offen halten"-Klicks
    autoclose_warn_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    autoclose_keepopen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    columns: Mapped[list[IncidentColumn]] = relationship(
        back_populates="incident", order_by="IncidentColumn.display_order", cascade="all, delete-orphan"
    )
    vehicles: Mapped[list[IncidentVehicle]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    tasks: Mapped[list[Task]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    messages: Mapped[list[Message]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    rescued_persons: Mapped[list[RescuedPerson]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    log_entries: Mapped[list[IncidentLog]] = relationship(
        back_populates="incident", order_by="IncidentLog.ts", cascade="all, delete-orphan"
    )
    changes: Mapped[list[IncidentChange]] = relationship(
        back_populates="incident", order_by="IncidentChange.ts", cascade="all, delete-orphan"
    )
    breathing_troops: Mapped[list[BreathingTroop]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    tokens: Mapped[list[IncidentToken]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    collaborating_orgs: Mapped[list[IncidentOrg]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    leader: Mapped[User | None] = relationship(
        "User", foreign_keys=[incident_leader_user_id], lazy="joined"
    )
    leader_member: Mapped[object | None] = relationship(
        "Member", foreign_keys=[incident_leader_member_id], lazy="joined"
    )


class IncidentOrg(Base):
    """Organisations, die an einem Einsatz beteiligt sind (multi-org collaboration)."""
    __tablename__ = "incident_org"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    # role: 'leader' (primary org) or 'collaborator'
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="collaborator")
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    added_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)

    incident: Mapped[Incident] = relationship(back_populates="collaborating_orgs")


class IncidentColumn(Base):
    __tablename__ = "incident_column"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    is_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    incident: Mapped[Incident] = relationship(back_populates="columns")
    vehicles: Mapped[list[IncidentVehicle]] = relationship(back_populates="column")
    tasks: Mapped[list[Task]] = relationship(
        back_populates="column",
        primaryjoin="and_(Task.column_id==IncidentColumn.id, Task.vehicle_id==None)",
        foreign_keys="Task.column_id",
        overlaps="column"
    )


class IncidentVehicle(Base):
    __tablename__ = "incident_vehicle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    column_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident_column.id"), nullable=False)
    vehicle_master_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("vehicle_master.id"), nullable=False)
    commander_member_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    org_color_override: Mapped[str | None] = mapped_column(String(7), nullable=True)
    unit_status: Mapped[str] = mapped_column(String(40), nullable=False, default="Einsatz übernommen")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    incident: Mapped[Incident] = relationship(back_populates="vehicles")
    column: Mapped[IncidentColumn] = relationship(back_populates="vehicles")
    vehicle_master: Mapped[VehicleMaster] = relationship(lazy="joined")
    commander: Mapped[Member | None] = relationship(foreign_keys=[commander_member_id], lazy="joined")
    assigned_tasks: Mapped[list[Task]] = relationship(
        back_populates="vehicle",
        primaryjoin="Task.vehicle_id==IncidentVehicle.id",
        foreign_keys="Task.vehicle_id",
    )
    assigned_messages: Mapped[list[Message]] = relationship(
        primaryjoin="Message.vehicle_id==IncidentVehicle.id",
        foreign_keys="Message.vehicle_id",
        viewonly=True,
    )
    assigned_persons: Mapped[list[RescuedPerson]] = relationship(
        primaryjoin="RescuedPerson.vehicle_id==IncidentVehicle.id",
        foreign_keys="RescuedPerson.vehicle_id",
        viewonly=True,
    )

    @property
    def open_task_count(self) -> int:
        return sum(1 for t in self.assigned_tasks if not t.is_done and not t.is_cancelled)

    @property
    def status_color(self) -> str:
        n = self.open_task_count
        if n == 0:
            return "green"
        if n == 1:
            return "yellow"
        return "red"


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    column_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incident_column.id"), nullable=True)
    vehicle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incident_vehicle.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)

    incident: Mapped[Incident] = relationship(back_populates="tasks")
    column: Mapped[IncidentColumn | None] = relationship(
        back_populates="tasks",
        primaryjoin="and_(Task.column_id==IncidentColumn.id, Task.vehicle_id==None)",
        foreign_keys=[column_id],
        overlaps="tasks"
    )
    vehicle: Mapped[IncidentVehicle | None] = relationship(
        back_populates="assigned_tasks",
        foreign_keys=[vehicle_id],
    )
    media: Mapped[list[TaskMedia]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskMedia.created_at.desc()",
    )


class TaskMedia(Base):
    """Bilder / PDFs / Videos, die einem Auftrag (Task) angehaengt sind.

    Dateien liegen unter settings.MEDIA_STORAGE_DIR (ausserhalb von app/static)
    und werden nur ueber die geschuetzte Route /medien/datei/{id} ausgeliefert.
    """
    __tablename__ = "task_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)   # image | pdf | video
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    thumb_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), index=True,
    )

    task: Mapped[Task] = relationship(back_populates="media")


class MessageMedia(Base):
    """Bilder / PDFs / Videos, die einer Meldung (Message) angehaengt sind."""
    __tablename__ = "message_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("message.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    thumb_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), index=True,
    )


class PersonMedia(Base):
    """Bilder / PDFs / Videos, die einer geretteten Person angehaengt sind."""
    __tablename__ = "person_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rescued_person.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    thumb_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), index=True,
    )


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    vehicle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incident_vehicle.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_after_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    popup_shown: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    incident: Mapped[Incident] = relationship(back_populates="messages")
    vehicle: Mapped[IncidentVehicle | None] = relationship(foreign_keys=[vehicle_id])
    media: Mapped[list[MessageMedia]] = relationship(
        cascade="all, delete-orphan",
        order_by="MessageMedia.created_at.desc()",
        primaryjoin="Message.id==MessageMedia.message_id",
        foreign_keys="MessageMedia.message_id",
    )


class RescuedPerson(Base):
    __tablename__ = "rescued_person"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    gender: Mapped[str] = mapped_column(String(30), nullable=False, default="Unbekannt")
    person_group: Mapped[str] = mapped_column(String(30), nullable=False, default="Erwachsen")
    age_range: Mapped[str | None] = mapped_column(String(30), nullable=True)
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    vehicle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incident_vehicle.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="gefunden")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    incident: Mapped[Incident] = relationship(back_populates="rescued_persons")
    media: Mapped[list[PersonMedia]] = relationship(
        cascade="all, delete-orphan",
        order_by="PersonMedia.created_at.desc()",
        primaryjoin="RescuedPerson.id==PersonMedia.person_id",
        foreign_keys="PersonMedia.person_id",
    )


class IncidentLog(Base):
    __tablename__ = "incident_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    level: Mapped[str] = mapped_column(String(10), default="info")
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    incident: Mapped[Incident] = relationship(back_populates="log_entries")


class IncidentChange(Base):
    """Granular per-field change log — replaces snapshot approach."""
    __tablename__ = "incident_change"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    api_key_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("api_key.id"), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(50), nullable=True)

    incident: Mapped[Incident] = relationship(back_populates="changes")


class IncidentToken(Base):
    """QR-Code token valid for the lifetime of the incident."""
    __tablename__ = "incident_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issued_by_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    incident: Mapped[Incident] = relationship(back_populates="tokens")


# Import BreathingTroop here to avoid circular import in Incident.breathing_troops
from app.models.breathing import BreathingTroop  # noqa: E402
