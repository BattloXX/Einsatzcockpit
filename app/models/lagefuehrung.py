"""Lageführung-Modul (Phase 1 / MVP): einsatzbezogene Lagekarte.

`LagefuehrungFeature` speichert alles, was der Lageführer manuell auf die Karte
bringt (Zeichnungen, Marker, Text). `LagefuehrungEvent` ist ein append-only
Ereignisprotokoll (Fundament für Chronologie-UI und Replay in Phase 2/3).

Beide Tabellen sind TenantScoped (org-isoliert) und in _TENANT_TABLE_NAMES
(app/core/tenant.py) registriert.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base

# typ-Werte: 'zeichnung'/'marker'/'text' sind in Phase 1 nutzbar; 'taktisches_zeichen',
# 'meldung', 'distanz' sind für Phase 2 reserviert (kein Migrations-Bruch nötig).
LAGEFUEHRUNG_FEATURE_TYPEN = (
    "zeichnung",
    "marker",
    "text",
    "taktisches_zeichen",
    "meldung",
    "distanz",
)


class LagefuehrungFeature(TenantScoped, Base):
    """Ein von Hand gesetztes Objekt auf der Lagekarte (Zeichnung/Marker/Text)."""
    __tablename__ = "lagefuehrung_feature"
    __table_args__ = (
        Index("ix_lagefuehrung_feature_org_incident", "org_id", "incident_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False
    )
    typ: Mapped[str] = mapped_column(String(30), nullable=False)
    zeichen_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # GeoJSON (Point/LineString/Polygon), Muster Sector.geometry
    geometry: Mapped[str] = mapped_column(Text, nullable=False)
    rotation: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    scale: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=1.0)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    props: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: Farbe, Strichstärke, ...
    layer_gruppe: Mapped[str] = mapped_column(String(32), nullable=False, default="zeichnung")
    # Optimistic Concurrency (Fundament für Multi-User-Editing in Phase 2)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LagefuehrungEvent(Base, TenantScoped):
    """Append-only Ereignisprotokoll je Einsatz (Chronologie/Replay-Fundament)."""
    __tablename__ = "lagefuehrung_event"
    __table_args__ = (
        Index("ix_lagefuehrung_event_org_incident_ts", "org_id", "incident_id", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    event_typ: Mapped[str] = mapped_column(String(48), nullable=False)
    ref_typ: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON


class LagefuehrungBerechtigung(TenantScoped, Base):
    """Vom Lageführer explizit vergebene Editor-Rechte (Phase 2, F10).

    Ergänzt (ersetzt nicht) die rollenbasierte Editierberechtigung aus Phase 1:
    ein Viewer ohne Editor-Rolle kann hierüber trotzdem zum Editor ernannt werden.
    """
    __tablename__ = "lagefuehrung_berechtigung"
    __table_args__ = (
        UniqueConstraint("incident_id", "user_id", name="uq_lagefuehrung_berechtigung"),
        Index("ix_lagefuehrung_berechtigung_org_incident", "org_id", "incident_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    granted_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class LagefuehrungSnapshot(TenantScoped, Base):
    """Momentaufnahme ('Lage einfrieren', Phase 3, F-Snapshot): PNG-Export des Kartenstands
    zu einem Zeitpunkt, an ein lagefuehrung_event (event_typ='snapshot.erstellt') verlinkt.
    """
    __tablename__ = "lagefuehrung_snapshot"
    __table_args__ = (
        Index("ix_lagefuehrung_snapshot_org_incident", "org_id", "incident_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False
    )
    stored_filename: Mapped[str] = mapped_column(String(80), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
