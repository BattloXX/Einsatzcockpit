"""Geräteverleih-Modul – Datenmodelle.

Alle Tabellen sind org-scoped (TenantScoped-Mixin).
"""
from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base


class VerleihStatus(enum.StrEnum):
    ausgeliehen    = "ausgeliehen"
    zurueckgegeben = "zurueckgegeben"


class ArtikelVerfuegbarkeit(enum.StrEnum):
    verfuegbar  = "verfuegbar"
    ausgeliehen = "ausgeliehen"


class VerleihGeraetetyp(TenantScoped, Base):
    """Geraetetyp – kategorisiert Artikel (z.B. 'Pumpe', 'Atemschutzgeraet')."""
    __tablename__ = "verleih_geraetetyp"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    beschreibung: Mapped[str | None] = mapped_column(Text, nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    artikel: Mapped[list[VerleihArtikel]] = relationship(
        back_populates="geraetetyp", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_verleih_geraetetyp_org", "org_id"),
    )


class VerleihArtikel(TenantScoped, Base):
    """Artikelstammdaten – org-weit, zwei Typen: eindeutig vs. Menge."""
    __tablename__ = "verleih_artikel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    artikel_nr: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bezeichnung: Mapped[str] = mapped_column(String(200), nullable=False)
    geraetetyp_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("verleih_geraetetyp.id", ondelete="SET NULL"), nullable=True
    )
    # False = eindeutiger Artikel (z.B. "Pumpe 2"), Menge immer 1
    # True  = Mengenartikel (z.B. "C-Schlauch"),   Menge frei waehlbar
    ist_mengenartikel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Nur fuer eindeutige Artikel relevant (ist_mengenartikel=False)
    verfuegbarkeit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lagerbestand: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notizen: Mapped[str | None] = mapped_column(Text, nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    geraetetyp: Mapped[VerleihGeraetetyp | None] = relationship(back_populates="artikel")
    stueckliste_positionen: Mapped[list[VerleihStuecklistePosition]] = relationship(
        back_populates="artikel", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_verleih_artikel_org", "org_id"),
    )


class VerleihStueckliste(TenantScoped, Base):
    """Vorkonfigurierte Artikel-Kombination (z.B. Hochwasser-Grundausstattung)."""
    __tablename__ = "verleih_stueckliste"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    bezeichnung: Mapped[str] = mapped_column(String(200), nullable=False)
    notizen: Mapped[str | None] = mapped_column(Text, nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    positionen: Mapped[list[VerleihStuecklistePosition]] = relationship(
        back_populates="stueckliste", cascade="all, delete-orphan", order_by="VerleihStuecklistePosition.id"
    )

    __table_args__ = (
        Index("ix_verleih_stueckliste_org", "org_id"),
    )


class VerleihStuecklistePosition(Base):
    """Eine Position innerhalb einer Stückliste."""
    __tablename__ = "verleih_stueckliste_position"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stueckliste_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("verleih_stueckliste.id", ondelete="CASCADE"), nullable=False
    )
    artikel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("verleih_artikel.id", ondelete="SET NULL"), nullable=True
    )
    # Wenn geraetetyp_id gesetzt: beim Ausleihen muss ein konkreter Artikel des Typs gewaehlt werden
    geraetetyp_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("verleih_geraetetyp.id", ondelete="SET NULL"), nullable=True
    )
    bezeichnung: Mapped[str | None] = mapped_column(String(200), nullable=True)
    artikel_nr: Mapped[str | None] = mapped_column(String(100), nullable=True)
    menge: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    stueckliste: Mapped[VerleihStueckliste] = relationship(back_populates="positionen")
    artikel: Mapped[VerleihArtikel | None] = relationship(back_populates="stueckliste_positionen")
    geraetetyp: Mapped[VerleihGeraetetyp | None] = relationship()


class VerleihAusleihe(TenantScoped, Base):
    """Verleih-Transaktion – eine Ausleihe pro Ausleiher, enthält mehrere Positionen."""
    __tablename__ = "verleih_ausleihe"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    lage_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("major_incident.id", ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident_site.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    adresse: Mapped[str | None] = mapped_column(String(300), nullable=True)
    telefon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notizen: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[VerleihStatus] = mapped_column(
        Enum(VerleihStatus), nullable=False, default=VerleihStatus.ausgeliehen
    )
    pin: Mapped[str | None] = mapped_column(String(6), nullable=True)
    sms_ausleih_gesendet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    erinnerung_geplant_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    erinnerung_gesendet_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ausgeliehen_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    zurueckgegeben_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    positionen: Mapped[list[VerleihPosition]] = relationship(
        back_populates="ausleihe", cascade="all, delete-orphan", order_by="VerleihPosition.id"
    )
    fotos: Mapped[list[VerleihFoto]] = relationship(
        back_populates="ausleihe", cascade="all, delete-orphan", order_by="VerleihFoto.id"
    )

    __table_args__ = (
        Index("ix_verleih_ausleihe_lage", "lage_id"),
        Index("ix_verleih_ausleihe_org", "org_id"),
        Index("ix_verleih_ausleihe_erinnerung", "erinnerung_geplant_at"),
    )

    @property
    def offene_positionen(self) -> int:
        return sum(1 for p in self.positionen if p.status == VerleihStatus.ausgeliehen)

    @property
    def artikel_bezeichnungen(self) -> str:
        return ", ".join(p.bezeichnung for p in self.positionen)


class VerleihPosition(Base):
    """Ein Artikel innerhalb einer VerleihAusleihe."""
    __tablename__ = "verleih_position"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ausleihe_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("verleih_ausleihe.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artikel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("verleih_artikel.id", ondelete="SET NULL"), nullable=True
    )
    bezeichnung: Mapped[str] = mapped_column(String(200), nullable=False)
    artikel_nr: Mapped[str | None] = mapped_column(String(100), nullable=True)
    menge: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[VerleihStatus] = mapped_column(
        Enum(VerleihStatus), nullable=False, default=VerleihStatus.ausgeliehen
    )
    zurueckgegeben_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    ausleihe: Mapped[VerleihAusleihe] = relationship(back_populates="positionen")
    artikel: Mapped[VerleihArtikel | None] = relationship()

    __table_args__ = (
        Index("ix_verleih_position_ausleihe", "ausleihe_id"),
    )


class VerleihFoto(Base):
    """Beweis-Foto einer Ausleihe – dokumentiert was ausgegeben wurde."""
    __tablename__ = "verleih_foto"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ausleihe_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("verleih_ausleihe.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stored_filename: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    uploaded_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    ausleihe: Mapped[VerleihAusleihe] = relationship(back_populates="fotos")

    __table_args__ = (
        Index("ix_verleih_foto_ausleihe", "ausleihe_id"),
    )
