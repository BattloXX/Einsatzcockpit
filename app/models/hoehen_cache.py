"""Persistenter Höhen-Cache (global, nicht org-gebunden).

Geländehöhen je Koordinate sind geografische Fakten ohne Org-Bezug — daher NICHT
TenantScoped und NICHT in _TENANT_TABLE_NAMES. Schlüssel ist die auf ~1 m gerundete
Koordinate (lat/lng · 1e5 als Integer), damit Übungs-/Planungsrouten, die oft mehrfach
gerechnet werden, ohne erneute HTTP-Abfrage bedient werden.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Rundungsfaktor: 1e5 ≈ 1 m Auflösung in lat/lng
HOEHEN_RASTER = 100000


def hoehen_key(lat: float, lng: float) -> tuple[int, int]:
    """Rundet eine Koordinate auf das Cache-Raster (~1 m)."""
    return (round(lat * HOEHEN_RASTER), round(lng * HOEHEN_RASTER))


class HoehenCache(Base):
    """Gecachte Geländehöhe je gerundeter Koordinate (global)."""

    __tablename__ = "hoehen_cache"
    __table_args__ = (
        UniqueConstraint("lat_key", "lng_key", name="uq_hoehen_cache_koord"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lat_key: Mapped[int] = mapped_column(Integer, nullable=False)
    lng_key: Mapped[int] = mapped_column(Integer, nullable=False)
    hoehe_m: Mapped[float] = mapped_column(Float, nullable=False)
    quelle: Mapped[str] = mapped_column(String(20), nullable=False, default="openmeteo")
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
