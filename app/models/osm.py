"""Globale oeffentliche OSM-Daten, bewusst ohne TenantScoped."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class OsmImportLauf(Base):
    __tablename__ = "osm_import_lauf"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    quelle: Mapped[str] = mapped_column(String(40))
    url: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20))
    gestartet_am: Mapped[datetime] = mapped_column(DateTime)
    beendet_am: Mapped[datetime | None] = mapped_column(DateTime)
    datenstand: Mapped[datetime | None] = mapped_column(DateTime)
    anzahl: Mapped[int] = mapped_column(Integer, default=0)
    neu: Mapped[int] = mapped_column(Integer, default=0)
    geaendert: Mapped[int] = mapped_column(Integer, default=0)
    entfernt: Mapped[int] = mapped_column(Integer, default=0)
    min_lat: Mapped[float | None] = mapped_column(Float)
    max_lat: Mapped[float | None] = mapped_column(Float)
    min_lng: Mapped[float | None] = mapped_column(Float)
    max_lng: Mapped[float | None] = mapped_column(Float)
    fehler_code: Mapped[str | None] = mapped_column(String(60))
    fehler_text: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (Index("ix_osm_import_lauf_quelle_status", "quelle", "status"),)


class OsmGebaeude(Base):
    __tablename__ = "osm_gebaeude"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lauf_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("osm_import_lauf.id", ondelete="CASCADE"))
    osm_way_id: Mapped[int] = mapped_column(BigInteger)
    ecken_json: Mapped[str] = mapped_column(Text)
    ecken_hash: Mapped[str] = mapped_column(String(64))
    min_lat: Mapped[float] = mapped_column(Float)
    max_lat: Mapped[float] = mapped_column(Float)
    min_lng: Mapped[float] = mapped_column(Float)
    max_lng: Mapped[float] = mapped_column(Float)
    __table_args__ = (
        Index("ix_osm_gebaeude_lauf_position", "lauf_id", "min_lat", "min_lng"),
        Index("uq_osm_gebaeude_lauf_way", "lauf_id", "osm_way_id", unique=True),
    )
