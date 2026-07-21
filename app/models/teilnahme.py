"""Teilnehmerlisten: Termin (Übung/Veranstaltung), Funktion, Teilnahme."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
    status: Mapped[str] = mapped_column(
        Enum("geplant", "laufend", "abgeschlossen", "abgesagt"),
        default="geplant",
        nullable=False,
    )
    erstellt_von: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    @property
    def typ_label(self) -> str:
        return "Übung" if self.typ == "uebung" else "Veranstaltung"

    @property
    def status_label(self) -> str:
        return {
            "geplant": "Geplant",
            "laufend": "Laufend",
            "abgeschlossen": "Abgeschlossen",
            "abgesagt": "Abgesagt",
        }.get(self.status, self.status)

    @property
    def status_css(self) -> str:
        return {
            "geplant": "status-pill--blue",
            "laufend": "status-pill--green",
            "abgeschlossen": "status-pill--muted",
            "abgesagt": "status-pill--red",
        }.get(self.status, "")


class Funktion(TenantScoped, Base):
    __tablename__ = "funktion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sortierung: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Teilnahme(TenantScoped, Base):
    __tablename__ = "teilnahme"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "bezug_typ", "bezug_id", "mitglied_id",
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
    funktion: Mapped[Funktion | None] = relationship(
        "Funktion", lazy="joined", foreign_keys="[Teilnahme.funktion_id]"
    )
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
