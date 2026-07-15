"""Modelle für lokale Wetterstationen (Davis Vantage Pro 2 / Meteobridge).

Aufteilung (siehe docs/wetterstation-konzept.md):

- ``WeatherStation`` liegt in der HAUPT-DB und ist tenant-scoped (``TenantScoped``)
  → jede Organisation kann ihre eigene(n) Station(en) einbinden, Org-Isolation
  garantiert der globale do_orm_execute-Listener (Fail-Closed). Die Tabelle hält
  Konfiguration UND den DENORMALISIERTEN letzten Messwert, damit die Wetter-Seite
  den Ist-Stand ohne Zugriff auf die große Zeitreihen-Tabelle anzeigen kann.

- ``WeatherReading`` liegt in der SEPARATEN Wetter-DB (``WeatherBase``) und enthält
  die Zeitreihe. Org-Isolation hier über explizites ``WHERE org_id`` im Service.
"""
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base
from app.db_weather import WeatherBase


class WeatherStation(Base, TenantScoped):
    """Konfiguration + letzter Messwert einer lokalen Wetterstation (Haupt-DB).

    ``org_id`` stammt aus dem ``TenantScoped``-Mixin → automatische Org-Filterung.
    """
    __tablename__ = "weather_station"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    quelle: Mapped[str] = mapped_column(String(50), nullable=False, default="davis_meteobridge")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Push-Token (Meteobridge → Cloud), nur als SHA256-Hash gespeichert.
    ingest_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    # Letzter erfolgreicher Push → Online/Offline-Ampel.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Zeitpunkt der letzten Messung (von der Station gemeldet).
    last_measured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── Denormalisierter letzter Messwert (Ist-Stand) ────────────────────────
    last_temp_c:        Mapped[float | None] = mapped_column(Float, nullable=True)
    last_hum_pct:       Mapped[float | None] = mapped_column(Float, nullable=True)
    last_wind_ms:       Mapped[float | None] = mapped_column(Float, nullable=True)
    last_gust_ms:       Mapped[float | None] = mapped_column(Float, nullable=True)
    last_wind_dir_deg:  Mapped[float | None] = mapped_column(Float, nullable=True)
    last_pressure_hpa:  Mapped[float | None] = mapped_column(Float, nullable=True)
    last_rain_rate_mmh: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_rain_day_mm:   Mapped[float | None] = mapped_column(Float, nullable=True)
    last_dewpoint_c:    Mapped[float | None] = mapped_column(Float, nullable=True)
    last_solar_wm2:     Mapped[float | None] = mapped_column(Float, nullable=True)
    last_uv:            Mapped[float | None] = mapped_column(Float, nullable=True)


class WeatherDashboardToken(Base):
    """Beschriftetes Token fuer den oeffentlichen, loginfreien Wetter-Zugriff
    (Infoscreen-Dashboard + JSON-Endpoint fuer externe Einbettung, z.B. das
    WordPress-Widget der Vereins-Website). Mehrere Tokens je Org moeglich, damit sich
    z.B. Infoscreen und Website-Widget unabhaengig voneinander loeschen/erneuern
    lassen, ohne den jeweils anderen Verbraucher zu invalidieren (Haupt-DB, plain
    ``Base`` wie ``LagekarteToken``/``ApiKey`` -- der Lookup erfolgt vor jeder
    Authentifizierung, also ohne Tenant-Kontext)."""
    __tablename__ = "weather_dashboard_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WeatherReading(WeatherBase):
    """Einzelne Messung einer Station (Zeitreihe, separate Wetter-DB)."""
    __tablename__ = "weather_reading"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Bewusst KEIN ForeignKey: liegt in separater DB ohne Zugriff auf fire_dept/weather_station.
    org_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    station_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    temp_c:        Mapped[float | None] = mapped_column(Float, nullable=True)
    hum_pct:       Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_ms:       Mapped[float | None] = mapped_column(Float, nullable=True)
    gust_ms:       Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_dir_deg:  Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure_hpa:  Mapped[float | None] = mapped_column(Float, nullable=True)
    rain_rate_mmh: Mapped[float | None] = mapped_column(Float, nullable=True)
    rain_day_mm:   Mapped[float | None] = mapped_column(Float, nullable=True)
    dewpoint_c:    Mapped[float | None] = mapped_column(Float, nullable=True)
    solar_wm2:     Mapped[float | None] = mapped_column(Float, nullable=True)
    uv:            Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        # Verlauf-Abfragen: je Org+Station chronologisch; Retention löscht über ts.
        Index("ix_weather_reading_org_station_ts", "org_id", "station_id", "ts"),
    )


class AbflussReading(WeatherBase):
    """Einzelne Pegelmessung (Zeitreihe, separate Wetter-DB).

    Persistiert die In-Memory-Verlaufsdaten aus abfluss_service über Neustarts hinweg.
    Org-Isolation über explizites WHERE org_id + hzbnr.
    """
    __tablename__ = "abfluss_reading"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    hzbnr: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    wert_m3s: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("ix_abfluss_reading_org_hzbnr_ts", "org_id", "hzbnr", "ts"),
    )
