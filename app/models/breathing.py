from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

TROOP_STATUSES = ["bereit", "im_einsatz", "rueckzug", "zurueck", "erholt"]
TROOP_STATUS_LABELS = {
    "bereit":      "Bereit",
    "im_einsatz":  "Im Einsatz",
    "rueckzug":    "Rückzug!",
    "zurueck":     "Zurück",
    "erholt":      "Erholt",
}

# (slug, label, planned_duration_min)
BOTTLE_PRESETS = [
    ("1x6",    "1 × 6 L (300 bar)",   33),
    ("1x6_8",  "1 × 6,8 L (300 bar)", 37),
    ("1x9",    "1 × 9 L (300 bar)",   50),
    ("manuell", "manuell",            None),
]
BOTTLE_PRESET_LABELS = {slug: label for slug, label, _ in BOTTLE_PRESETS}
BOTTLE_PRESET_DURATIONS = {slug: dur for slug, _, dur in BOTTLE_PRESETS}


class BreathingTroop(Base):
    __tablename__ = "breathing_troop"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    vehicle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incident_vehicle.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Trupp")
    unit_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="bereit")
    task_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Planned duration (from bottle preset or manual entry)
    planned_duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bottle_preset: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Location / Standort
    location_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Last situation report
    last_meldung_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_meldung_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Warning acknowledgements
    warn_one_third_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_max_time_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_withdraw_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Pressure tracking
    start_press_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    withdraw_press_calc: Mapped[float | None] = mapped_column(Float, nullable=True)
    withdraw_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    incident: Mapped[Incident] = relationship(back_populates="breathing_troops")  # type: ignore[name-defined]
    members: Mapped[list[TroopMember]] = relationship(
        back_populates="troop", cascade="all, delete-orphan", lazy="joined"
    )
    pressure_logs: Mapped[list[PressureLog]] = relationship(
        back_populates="troop", order_by="PressureLog.ts", cascade="all, delete-orphan"
    )

    @property
    def elapsed_seconds(self) -> int | None:
        if self.entry_at is None:
            return None
        end = self.back_at or datetime.now(UTC)
        entry = self.entry_at if self.entry_at.tzinfo else self.entry_at.replace(tzinfo=UTC)
        end = end if end.tzinfo else end.replace(tzinfo=UTC)
        return int((end - entry).total_seconds())

    @property
    def one_third_seconds(self) -> int | None:
        """1/3 of planned duration in seconds (= Lagemeldung-Schwelle)."""
        if self.planned_duration_min is None:
            return None
        return self.planned_duration_min * 20  # 60 / 3 = 20

    @property
    def max_seconds(self) -> int | None:
        """Full planned duration in seconds."""
        if self.planned_duration_min is None:
            return None
        return self.planned_duration_min * 60

    @property
    def lowest_current_pressure(self) -> float | None:
        if not self.pressure_logs:
            return None
        return min(pl.pressure_bar for pl in self.pressure_logs if pl.pressure_bar is not None)

    @property
    def bottle_label(self) -> str:
        if self.bottle_preset:
            return BOTTLE_PRESET_LABELS.get(self.bottle_preset, self.bottle_preset)
        return "–"


class TroopMember(Base):
    __tablename__ = "troop_member"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    troop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("breathing_troop.id", ondelete="CASCADE"), nullable=False)
    member_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("member.id"), nullable=True)
    free_text_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="truppmann")
    start_press: Mapped[float | None] = mapped_column(Float, nullable=True)
    withdraw_press: Mapped[float | None] = mapped_column(Float, nullable=True)
    back_press: Mapped[float | None] = mapped_column(Float, nullable=True)

    troop: Mapped[BreathingTroop] = relationship(back_populates="members")
    member: Mapped[Member | None] = relationship(lazy="joined")  # type: ignore[name-defined]

    @property
    def display_name(self) -> str:
        if self.member:
            return self.member.full_name
        return self.free_text_name or "Unbekannt"


class PressureLog(Base):
    __tablename__ = "pressure_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    troop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("breathing_troop.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    member_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("member.id"), nullable=True)
    pressure_bar: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    troop: Mapped[BreathingTroop] = relationship(back_populates="pressure_logs")


# Forward reference resolution
from app.models.incident import Incident  # noqa: E402, F401
