"""DIBOS EventHub / Elvis-Anbindung (Landeswarnzentrale Vorarlberg): Org-Konfiguration.

Anders als LIS/IPR (siehe app/services/lis/) ist DIBOS EventHub keine SOAP/WCF-
Schnittstelle, sondern liefert JSON über einen SOAP-Umschlag mit WS-Security-
UsernameToken im Header. Siehe app/services/dibos/ für Client und Tracing-Capture.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OrgDibosConfig(Base):
    """DIBOS-EventHub/Elvis-Verbindung einer Organisation (1:1 je Org). Muster: OrgLisConfig."""
    __tablename__ = "org_dibos_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # DIBOS-Verbindung
    base_url: Mapped[str | None] = mapped_column(
        String(300), nullable=True, default="https://dibos.lwz-vorarlberg.at/Z_EventHub"
    )
    host: Mapped[str] = mapped_column(String(100), nullable=False, default="einsatzcockpit")
    ag: Mapped[str] = mapped_column(String(10), nullable=False, default="FW")  # Agentur-Filter GetPublicEvents
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=20)

    # Diagnose (nur system_admin, siehe ui_dibos.py "Diagnose"-Sektion): startet die
    # Rohdaten-Aufzeichnung (dibos_capture.py) automatisch, sobald der leichte
    # Erkennungs-Loop (dibos_loop.py) einen eigenen aktiven Einsatz sieht (GetCurrentEvents
    # nicht leer) — spart den manuellen Klick genau während des Einsatzfensters.
    auto_trace_on_event: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_trace_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)

    # Explizites Opt-in: reichert einen per Einsatznummer (Incident.lis_operation_number)
    # gefundenen, AKTIVEN Einsatz mit Zusatzinfos aus GetCurrentEvents/GetCurrentUnits an
    # (Einsatzort, Einsatzcode/Diagnose, BMA-Nr., Meldungsprotokoll, Fahrzeug-Statuszeiten
    # — siehe app/services/dibos/dibos_enrich.py). Default False, damit DIBOS für
    # bestehende Orgs weiterhin ein reines Tracing/Diagnose-Feature bleibt, bis eine Org
    # das bewusst aktiviert.
    enrich_incidents: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Zugangsdaten: zwei getrennte Konten (siehe dibos_client.py)
    # 1) Gateway-Konto (HTTP-Basic, vom Betreiber vergeben, unabhängig von der Org)
    gateway_user: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gateway_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-verschlüsselt
    # 2) Org-Servicekonto (WS-Security-UsernameToken im SOAP-Body)
    service_user: Mapped[str | None] = mapped_column(String(150), nullable=True)
    service_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-verschlüsselt

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")

    @property
    def is_fully_configured(self) -> bool:
        return bool(
            self.base_url
            and self.gateway_user
            and self.gateway_password_enc
            and self.service_user
            and self.service_password_enc
        )
