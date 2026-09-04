"""Teilnehmerlisten: Termin (Übung/Veranstaltung), Funktion, Teilnahme."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base

if TYPE_CHECKING:
    from app.models.master import Member, VehicleMaster
    from app.models.user import User


class Termin(TenantScoped, Base):
    __tablename__ = "termin"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    typ: Mapped[str] = mapped_column(Enum("uebung", "veranstaltung"), nullable=False)
    titel: Mapped[str] = mapped_column(String(200), nullable=False)
    beschreibung: Mapped[str | None] = mapped_column(Text, nullable=True)
    ort: Mapped[str | None] = mapped_column(String(200), nullable=True)
    beginn: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ende: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ganztaegig: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="geplant", nullable=False)
    erstellt_von: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    probeart_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("probeart.id", ondelete="SET NULL"))
    thema: Mapped[str | None] = mapped_column(String(200))
    objekt: Mapped[str | None] = mapped_column(String(200))
    objekt_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("objekt.id", ondelete="SET NULL"))
    info: Mapped[str | None] = mapped_column(Text)
    interne_bemerkung: Mapped[str | None] = mapped_column(Text)
    verantwortlich_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL")
    )
    unterstuetzung_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL")
    )
    alarmtext: Mapped[str | None] = mapped_column(Text)
    besondere_gefahren: Mapped[str | None] = mapped_column(Text)
    besondere_hinweise: Mapped[str | None] = mapped_column(Text)
    public_sichtbar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    public_ort_sichtbar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_info_sichtbar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ics_uid: Mapped[str | None] = mapped_column(String(80), unique=True, default=lambda: uuid4().hex)
    ics_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    geaendert_am: Mapped[datetime | None] = mapped_column(DateTime, onupdate=lambda: datetime.now(UTC))
    exercise_incident_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="SET NULL"))
    archiviert_am: Mapped[datetime | None] = mapped_column(DateTime)
    vorbereitung_uebersteuert_von: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL")
    )
    vorbereitung_uebersteuert_am: Mapped[datetime | None] = mapped_column(DateTime)
    vorbereitung_uebersteuert_grund: Mapped[str | None] = mapped_column(Text)

    @property
    def typ_label(self) -> str:
        return "Übung" if self.typ == "uebung" else "Veranstaltung"

    @property
    def status_label(self) -> str:
        return {
            "entwurf": "Entwurf",
            "geplant": "Geplant",
            "in_vorbereitung": "In Vorbereitung",
            "vorbereitung_abgeschlossen": "Vorbereitung abgeschlossen",
            "durchfuehrung_laeuft": "Durchführung läuft",
            "durchgefuehrt": "Durchgeführt",
            "abgeschlossen": "Abgeschlossen",
            "abgesagt": "Abgesagt",
        }.get(self.status, self.status)

    @property
    def status_css(self) -> str:
        return {
            "entwurf": "status-pill--muted",
            "geplant": "status-pill--blue",
            "in_vorbereitung": "status-pill--blue",
            "vorbereitung_abgeschlossen": "status-pill--green",
            "durchfuehrung_laeuft": "status-pill--green",
            "durchgefuehrt": "status-pill--green",
            "abgeschlossen": "status-pill--muted",
            "abgesagt": "status-pill--red",
        }.get(self.status, "")

    @property
    def ist_vollprobe(self) -> bool:
        probeart = getattr(self, "probeart", None)
        return bool(probeart and probeart.name.casefold() == "vollprobe")

    probeart = relationship("Probeart", foreign_keys=[probeart_id])


class Funktion(TenantScoped, Base):
    __tablename__ = "funktion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sortierung: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TeilnahmeStatus(StrEnum):
    NICHT_ERFASST = "nicht_erfasst"
    ANWESEND = "anwesend"
    ENTSCHULDIGT = "entschuldigt"
    UNENTSCHULDIGT = "unentschuldigt"


class Teilnahme(TenantScoped, Base):
    __tablename__ = "teilnahme"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "bezug_typ",
            "bezug_id",
            "mitglied_id",
            name="uq_teilnahme_mitglied",
        ),
        # DIBOS-Personenrückmeldung (personResponseList[].id) als zusätzlicher,
        # von mitglied_id UNABHÄNGIGER Upsert-Schlüssel — nötig, weil eine
        # Rückmeldung ohne Mitglied-Zuordnung (kein sybos_id-Match, siehe
        # dibos_enrich.py) mitglied_id=NULL hat und die Constraint oben (NULL
        # zählt in MySQL/SQLite je einzeln als eindeutig) sonst bei jedem Poll
        # eine neue Teilnahme-Zeile für dieselbe Person anlegen würde.
        UniqueConstraint("org_id", "dibos_response_id", name="uq_teilnahme_dibos_response"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    bezug_typ: Mapped[str] = mapped_column(Enum("einsatz", "uebung", "veranstaltung"), nullable=False)
    bezug_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mitglied_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="CASCADE"), nullable=True
    )
    freitext_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    funktion_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("funktion.id", ondelete="SET NULL"), nullable=True
    )
    fahrzeug_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("vehicle_master.id", ondelete="SET NULL"), nullable=True
    )
    notiz: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ausgerueckt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    entschuldigt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="nicht_erfasst")
    gekommen_um: Mapped[datetime | None] = mapped_column(DateTime)
    gegangen_um: Mapped[datetime | None] = mapped_column(DateTime)
    # RSVP (Zu-/Absage) über die Teams-Alarmierung — unabhängig von ausgerueckt/entschuldigt,
    # die erst nachträglich (tatsächliche Teilnahme) gepflegt werden. NULL = keine Antwort.
    rsvp_status: Mapped[str | None] = mapped_column(Enum("zugesagt", "abgesagt"), nullable=True)
    rsvp_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rsvp_source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # z.B. "teams", "dibos"
    # DIBOS-EventHub: LWZ-Rückmelde-ID (personResponseList[].id) — stabiler Upsert-
    # Schlüssel für Personenrückmeldungen ohne Mitglied-Zuordnung, siehe __table_args__.
    dibos_response_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    hinzugefuegt_von: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    hinzugefuegt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    mitglied: Mapped[Member | None] = relationship(  # type: ignore[name-defined]
        "Member", lazy="joined", foreign_keys="[Teilnahme.mitglied_id]"
    )
    funktion: Mapped[Funktion | None] = relationship("Funktion", lazy="joined", foreign_keys="[Teilnahme.funktion_id]")
    fahrzeug: Mapped[VehicleMaster | None] = relationship(  # type: ignore[name-defined]
        "VehicleMaster", lazy="joined", foreign_keys="[Teilnahme.fahrzeug_id]"
    )
    hinzugefuegt_von_user: Mapped[User | None] = relationship(  # type: ignore[name-defined]
        "User", lazy="joined", foreign_keys="[Teilnahme.hinzugefuegt_von]"
    )

    @property
    def anzeige_name(self) -> str:
        if self.mitglied:
            return self.mitglied.full_name
        return self.freitext_name or "–"

    @property
    def status_label(self) -> str:
        return {
            TeilnahmeStatus.NICHT_ERFASST.value: "Noch nicht erfasst",
            TeilnahmeStatus.ANWESEND.value: "Anwesend",
            TeilnahmeStatus.ENTSCHULDIGT.value: "Entschuldigt",
            TeilnahmeStatus.UNENTSCHULDIGT.value: "Unentschuldigt",
        }.get(self.status, self.status)

    @property
    def status_css(self) -> str:
        return {
            TeilnahmeStatus.NICHT_ERFASST.value: "status-pill--muted",
            TeilnahmeStatus.ANWESEND.value: "status-pill--green",
            TeilnahmeStatus.ENTSCHULDIGT.value: "status-pill--blue",
            TeilnahmeStatus.UNENTSCHULDIGT.value: "status-pill--red",
        }.get(self.status, "")

    def set_status(self, status: str | TeilnahmeStatus) -> None:
        """Setzt den Erfassungsstatus und hält die von Altansichten gelesenen Felder synchron."""
        try:
            normalized = TeilnahmeStatus(status)
        except ValueError as exc:
            raise ValueError("Ungültiger Teilnahmestatus") from exc
        self.status = normalized.value
        self.ausgerueckt = normalized is TeilnahmeStatus.ANWESEND
        self.entschuldigt = normalized is TeilnahmeStatus.ENTSCHULDIGT
