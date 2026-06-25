"""Wetterwarnungen: Regeln, Laufzeitzustand und Versandprotokoll (TenantScoped)."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.core.tenant import TenantScoped


class WeatherAlertRule(Base, TenantScoped):
    """Konfiguration je Org & Regeltyp – wann und wie wird gewarnt."""
    __tablename__ = "weather_alert_rule"
    __table_args__ = (
        UniqueConstraint("org_id", "key", name="uq_weather_alert_rule_org_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Regeltyp: 'sturm'|'starkregen'|'schneefall'|'glatteis'|'gewitter'|
    #            'lake_effekt'|'amtlich'|'foehn'|'waldbrand'|'tauwetter'|'downburst'
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vorwarnung: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    eskalation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    channel_mail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    channel_teams: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mail_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    teams_webhook_override: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cooldown_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )


class WeatherAlertState(Base, TenantScoped):
    """Laufzeitzustand je Org & Regeltyp – Dedup, Hysterese, letzte Benachrichtigung."""
    __tablename__ = "weather_alert_state"
    __table_args__ = (
        UniqueConstraint("org_id", "key", name="uq_weather_alert_state_org_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    # none | vorwarnung | akut
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Hysterese-Zähler: wie viele Loop-Zyklen liegt der Wert unter der Akut-Schwelle
    below_threshold_cycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WeatherAlertLog(Base, TenantScoped):
    """Versandprotokoll – Audit-Trail für alle ausgelösten Wetterwarnungen."""
    __tablename__ = "weather_alert_log"
    __table_args__ = (
        Index("ix_weather_alert_log_org_sent", "org_id", "gesendet_am"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    kanal: Mapped[str] = mapped_column(String(16), nullable=False)   # 'mail' | 'teams'
    empfaenger: Mapped[str] = mapped_column(String(255), nullable=False)
    betreff: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # 'gesendet' | 'fehler'
    fehlertext: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payload_excerpt: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    gesendet_am: Mapped[datetime] = mapped_column(DateTime, nullable=False)
