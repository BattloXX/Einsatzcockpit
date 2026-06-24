"""Datenmodell: Digitales Fahrten- & Betriebsbuch."""
from __future__ import annotations

import enum
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base


class FahrtKategorie(str, enum.Enum):
    einsatz = "einsatz"
    uebung = "uebung"
    sonstige = "sonstige"


class FahrtStatus(str, enum.Enum):
    aktiv = "aktiv"
    storniert = "storniert"
    ersetzt = "ersetzt"


class FahrtErfassungsweg(str, enum.Enum):
    web = "web"
    token = "token"


class Fahrtzweck(TenantScoped, Base):
    """Stammdaten: Fahrtzweck je Org."""
    __tablename__ = "fahrtzweck"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kategorie: Mapped[FahrtKategorie] = mapped_column(
        SAEnum(FahrtKategorie, name="fahrt_kategorie_enum"), nullable=False
    )
    verlangt_ausbildner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verlangt_gruppenkommandant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Zielort(TenantScoped, Base):
    """Stammdaten: Zielort je Org."""
    __tablename__ = "zielort"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Fahrt(Base):
    """Einzelner Fahrtenbuch-Eintrag."""
    __tablename__ = "fahrt"
    __table_args__ = (
        Index("ix_fahrt_org_zeitpunkt", "org_id", "zeitpunkt"),
        Index("ix_fahrt_fahrzeug", "fahrzeug_id"),
        Index("ix_fahrt_status", "status"),
        Index("ix_fahrt_maschinist", "maschinist_member_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    zeitpunkt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fahrzeug_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicle_master.id", ondelete="RESTRICT"), nullable=False
    )

    # Maschinist
    maschinist_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True
    )
    maschinist_name: Mapped[str] = mapped_column(String(160), nullable=False)
    maschinist2_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True
    )
    maschinist2_name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # km
    km_stand_neu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    km_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    km_warnung_bestaetigt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Betriebsstunden
    betriebsstunden_neu: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    betriebsstunden_delta: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    bh_warnung_bestaetigt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Seilwinde
    seilwinde_bh_neu: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    seilwinde_bh_delta: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    seilwinde_warnung_bestaetigt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    seilwinde_bediener_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True
    )
    seilwinde_bediener_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    seilwinde_zuege: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seilwinde_wartung: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Ziel & Zweck
    zielort_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("zielort.id", ondelete="SET NULL"), nullable=True
    )
    zielort_freitext: Mapped[str | None] = mapped_column(String(200), nullable=True)
    zweck_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fahrtzweck.id", ondelete="RESTRICT"), nullable=False
    )
    # Denormalisiert aus zweck.kategorie beim Speichern
    fahrttyp: Mapped[FahrtKategorie] = mapped_column(
        SAEnum(FahrtKategorie, name="fahrt_kategorie_enum"), nullable=False
    )
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="SET NULL"), nullable=True
    )

    # Bedingte Personen
    ausbildner_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True
    )
    ausbildner_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    gruppenkommandant_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True
    )
    gruppenkommandant_name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # Schaden
    schaden_vorhanden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    schaden_betriebsfaehig: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    schaden_beschreibung: Mapped[str | None] = mapped_column(Text, nullable=True)

    bemerkung: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Statistik & Workflow
    nicht_statistikrelevant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[FahrtStatus] = mapped_column(
        SAEnum(FahrtStatus, name="fahrt_status_enum"), nullable=False, default=FahrtStatus.aktiv
    )
    original_fahrt_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("fahrt.id", ondelete="SET NULL"), nullable=True
    )
    ersetzt_durch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("fahrt.id", ondelete="SET NULL"), nullable=True
    )
    storno_grund: Mapped[str | None] = mapped_column(Text, nullable=True)
    geaendert_von_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    # Erfassungskontext
    erfasst_von_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    erfasst_via: Mapped[FahrtErfassungsweg] = mapped_column(
        SAEnum(FahrtErfassungsweg, name="fahrt_erfassungsweg_enum"), nullable=False,
        default=FahrtErfassungsweg.web
    )
    token_label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Relationships
    fahrzeug: Mapped["VehicleMaster"] = relationship(foreign_keys=[fahrzeug_id])  # type: ignore[name-defined]
    zweck: Mapped[Fahrtzweck] = relationship(foreign_keys=[zweck_id])
    zielort: Mapped[Zielort | None] = relationship(foreign_keys=[zielort_id])
    benachrichtigungen: Mapped[list[FahrtBenachrichtigung]] = relationship(
        back_populates="fahrt", cascade="all, delete-orphan"
    )


class FahrtBenachrichtigung(Base):
    """Audit/Retry der Schadenmeldung (Mail & Teams)."""
    __tablename__ = "fahrt_benachrichtigung"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fahrt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fahrt.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    kanal: Mapped[str] = mapped_column(String(10), nullable=False)  # "mail" | "teams"
    empfaenger: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # "gesendet" | "fehler"
    fehlertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    gesendet_am: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    fahrt: Mapped[Fahrt] = relationship(back_populates="benachrichtigungen")
