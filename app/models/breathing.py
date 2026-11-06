from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.master import Member

WARNUNG_KINDS = [
    "withdraw", "max_exceeded", "one_third_due", "two_third_due",
    "funk_verloren", "notfall", "kein_sicherheitstrupp", "assp", "kein_heartbeat",
]

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
BOTTLE_PRESET_NOMINAL_BAR = {"1x6": 300, "1x6_8": 300, "1x9": 300, "manuell": None}


class BreathingAssp(Base):
    """Einsatzbezogene Logistik des Atemschutzsammelplatzes."""

    __tablename__ = "breathing_assp"
    __table_args__ = (UniqueConstraint("incident_id", name="uq_breathing_assp_incident"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False
    )
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    leiter_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    standort: Mapped[str | None] = mapped_column(String(200), nullable=True)
    flaschen_voll: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    flaschen_leer: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    notiz: Mapped[str | None] = mapped_column(String(300), nullable=True)
    aktiviert_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )


class BreathingTroop(Base):
    __tablename__ = "breathing_troop"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    vehicle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incident_vehicle.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Trupp")
    unit_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_sicherheitstrupp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="bereit")
    task_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Planned duration (from bottle preset or manual entry)
    planned_duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bottle_preset: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Location / Standort
    location_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ueberwacher_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    angriffsweg: Mapped[str | None] = mapped_column(String(300), nullable=True)
    funkkanal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    funk_verloren_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notfall_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ueberwacher_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    notfall_von_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    # Last situation report
    last_meldung_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_meldung_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Warning acknowledgements
    warn_one_third_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_two_third_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_max_time_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_withdraw_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_withdraw_acked_press: Mapped[float | None] = mapped_column(Float, nullable=True)
    readiness_override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    readiness_override_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True
    )
    readiness_override_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    deployments: Mapped[list[BreathingDeployment]] = relationship(
        back_populates="troop", order_by="BreathingDeployment.lfd_nr",
        cascade="all, delete-orphan", lazy="selectin",
    )

    kontrollen: Mapped[list[BreathingKontrolle]] = relationship(
        back_populates="troop", order_by="BreathingKontrolle.erledigt_at",
        cascade="all, delete-orphan", lazy="selectin",
    )

    @property
    def active_kontrollen(self) -> list[BreathingKontrolle]:
        """Kontrollen des aktuellen, noch nicht archivierten Einsatzzyklus."""
        return [kontrolle for kontrolle in self.kontrollen if kontrolle.deployment_id is None]

    @property
    def active_members(self) -> list[TroopMember]:
        """Mitglieder des aktuellen, noch nicht archivierten Einsatzzyklus."""
        return [member for member in self.members if member.deployment_id is None]

    @property
    def active_pressure_logs(self) -> list[PressureLog]:
        """Druckmeldungen des aktuellen, noch nicht archivierten Einsatzzyklus."""
        return [
            pressure_log for pressure_log in self.pressure_logs
            if pressure_log.troop_member.deployment_id is None
        ]

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
    def two_third_seconds(self) -> int | None:
        """2/3 der geplanten Einsatzzeit in Sekunden (= zweite Meldeschwelle)."""
        if self.planned_duration_min is None:
            return None
        return self.planned_duration_min * 40

    @property
    def lowest_current_pressure(self) -> float | None:
        pressures = [m.current_press for m in self.active_members if m.current_press is not None]
        if not pressures:
            return None
        return min(pressures)

    @property
    def bottle_label(self) -> str:
        if self.bottle_preset:
            return BOTTLE_PRESET_LABELS.get(self.bottle_preset, self.bottle_preset)
        return "–"

class BreathingDeployment(Base):
    """Unveränderlicher Snapshot eines abgeschlossenen Atemschutz-Einsatzzyklus."""

    __tablename__ = "breathing_deployment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    troop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("breathing_troop.id", ondelete="CASCADE"), nullable=False
    )
    lfd_nr: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    withdraw_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    planned_duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bottle_preset: Mapped[str | None] = mapped_column(String(20), nullable=True)
    task_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    location_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ueberwacher_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    angriffsweg: Mapped[str | None] = mapped_column(String(300), nullable=True)
    funkkanal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    funk_verloren_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notfall_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_press_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    withdraw_press_calc: Mapped[float | None] = mapped_column(Float, nullable=True)
    warn_one_third_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_two_third_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_max_time_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_withdraw_acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warn_withdraw_acked_press: Mapped[float | None] = mapped_column(Float, nullable=True)
    readiness_override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    readiness_override_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True
    )
    readiness_override_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    troop: Mapped[BreathingTroop] = relationship(back_populates="deployments")
    members: Mapped[list[TroopMember]] = relationship(
        back_populates="deployment", lazy="selectin"
    )
    pressure_logs: Mapped[list[PressureLog]] = relationship(
        secondary="troop_member",
        primaryjoin="BreathingDeployment.id == TroopMember.deployment_id",
        secondaryjoin="TroopMember.id == PressureLog.troop_member_id",
        order_by="PressureLog.ts",
        viewonly=True,
        lazy="selectin",
    )

    @property
    def one_third_seconds(self) -> int | None:
        if self.planned_duration_min is None:
            return None
        return self.planned_duration_min * 20

    @property
    def two_third_seconds(self) -> int | None:
        if self.planned_duration_min is None:
            return None
        return self.planned_duration_min * 40

    @property
    def max_seconds(self) -> int | None:
        if self.planned_duration_min is None:
            return None
        return self.planned_duration_min * 60

    @property
    def name(self) -> str:
        return self.troop.name

    @property
    def unit_name(self) -> str | None:
        return self.troop.unit_name

    @property
    def bottle_label(self) -> str:
        if self.bottle_preset:
            return BOTTLE_PRESET_LABELS.get(self.bottle_preset, self.bottle_preset)
        return "–"

    @property
    def active_members(self) -> list[TroopMember]:
        """Kompatibilitätsalias: ein Deployment enthält ausschließlich seinen Zyklus."""
        return self.members

    @property
    def active_pressure_logs(self) -> list[PressureLog]:
        """Kompatibilitätsalias: ein Deployment enthält ausschließlich seinen Zyklus."""
        return self.pressure_logs


class BreathingKontrolle(Base):
    __tablename__ = "breathing_kontrolle"
    __table_args__ = (Index("ix_breathing_kontrolle_troop_deployment", "troop_id", "deployment_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    troop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("breathing_troop.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("breathing_deployment.id", ondelete="CASCADE"), nullable=True
    )
    stufe: Mapped[str] = mapped_column(String(12), nullable=False)
    faellig_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    erledigt_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    nachgetragen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    niedrigster_druck: Mapped[float | None] = mapped_column(Float, nullable=True)
    troop_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("troop_member.id", ondelete="SET NULL"), nullable=True
    )
    art: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    troop: Mapped[BreathingTroop] = relationship(back_populates="kontrollen")


class TroopMember(Base):
    __tablename__ = "troop_member"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    troop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("breathing_troop.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("breathing_deployment.id", ondelete="CASCADE"), nullable=True
    )
    member_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("member.id"), nullable=True)
    free_text_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="truppmann")
    start_press: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_press: Mapped[float | None] = mapped_column(Float, nullable=True)
    objective_press: Mapped[float | None] = mapped_column(Float, nullable=True)
    objective_press_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    withdraw_press: Mapped[float | None] = mapped_column(Float, nullable=True)
    back_press: Mapped[float | None] = mapped_column(Float, nullable=True)

    troop: Mapped[BreathingTroop] = relationship(back_populates="members")
    deployment: Mapped[BreathingDeployment | None] = relationship(back_populates="members")
    member: Mapped[Member | None] = relationship(lazy="joined")  # type: ignore[name-defined]

    @property
    def display_name(self) -> str:
        if self.member:
            return self.member.full_name
        return self.free_text_name or "Unbekannt"


class PressureLog(Base):
    __tablename__ = "pressure_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    troop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("breathing_troop.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    member_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("member.id"), nullable=True)
    troop_member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("troop_member.id", ondelete="CASCADE"), nullable=False
    )
    nachgetragen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    pressure_bar: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    troop: Mapped[BreathingTroop] = relationship(back_populates="pressure_logs")
    troop_member: Mapped[TroopMember] = relationship()


# Forward reference resolution
from app.models.incident import Incident  # noqa: E402, F401


class BreathingWarnung(Base):
    __tablename__ = "breathing_warnung"
    __table_args__ = (Index("ix_breathing_warnung_troop_kind_erledigt", "troop_id", "kind", "erledigt_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    troop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("breathing_troop.id", ondelete="CASCADE"), nullable=False
    )
    zyklus_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    ausgeloest_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    erledigt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    eskalation_stufe: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    eskaliert_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    zustellungen: Mapped[list[BreathingZustellung]] = relationship(
        back_populates="warnung", cascade="all, delete-orphan",
    )


class BreathingZustellung(Base):
    __tablename__ = "breathing_zustellung"
    __table_args__ = (
        UniqueConstraint("warnung_id", "stufe", "kanal", "user_id", name="uq_breathing_zustellung_empfaenger"),
        Index("ix_breathing_zustellung_status_versuch", "status", "naechster_versuch_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    warnung_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("breathing_warnung.id", ondelete="CASCADE"), nullable=False
    )
    stufe: Mapped[int] = mapped_column(Integer, nullable=False)
    kanal: Mapped[str] = mapped_column(String(8), nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    empfaenger_label: Mapped[str | None] = mapped_column(String(150), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", server_default="pending")
    versuche: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    naechster_versuch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_bis: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fehler: Mapped[str | None] = mapped_column(String(300), nullable=True)
    gesendet_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    warnung: Mapped[BreathingWarnung] = relationship(back_populates="zustellungen")


class BreathingGeraetSitzung(Base):
    __tablename__ = "breathing_geraet_sitzung"
    __table_args__ = (
        Index("ix_breathing_sitzung_incident_ende_heartbeat", "incident_id", "beendet_at", "letzter_heartbeat_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    fcm_token_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("fcm_token.id", ondelete="SET NULL"), nullable=True
    )
    session_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    platform: Mapped[str] = mapped_column(String(10), nullable=False)
    geraet_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    selbsttest: Mapped[str | None] = mapped_column(Text, nullable=True)
    akku_prozent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    laedt: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gestartet_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    letzter_heartbeat_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    beendet_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ende_grund: Mapped[str | None] = mapped_column(String(20), nullable=True)


class BreathingClientEvent(Base):
    __tablename__ = "breathing_client_event"
    __table_args__ = (
        UniqueConstraint("incident_id", "client_event_id", name="uq_breathing_client_event_incident"),
        Index("ix_breathing_client_event_empfangen_at", "empfangen_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    client_event_id: Mapped[str] = mapped_column(String(40), nullable=False)
    route: Mapped[str] = mapped_column(String(30), nullable=False)
    erfasst_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    empfangen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    nachgetragen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
