"""Nur lesend gemeldete App-Diagnose, keine App-Konfiguration."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AppGeraet(Base):
    __tablename__ = "app_geraet"
    __table_args__ = (UniqueConstraint("user_id", "install_id", name="uq_app_geraet_install"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fire_dept.id"))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id", ondelete="CASCADE"))
    device_token_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("device_token.id", ondelete="SET NULL"))
    install_id: Mapped[str] = mapped_column(String(36))
    app_version: Mapped[str | None] = mapped_column(String(30), index=True)
    build_variant: Mapped[str | None] = mapped_column(String(20))
    hersteller: Mapped[str | None] = mapped_column(String(80))
    modell: Mapped[str | None] = mapped_column(String(80))
    geraeteklasse: Mapped[str | None] = mapped_column(String(10))
    android_version: Mapped[str | None] = mapped_column(String(40))
    sicherheitspatch: Mapped[str | None] = mapped_column(String(10))
    aufloesung: Mapped[str | None] = mapped_column(String(25))
    webview_version: Mapped[str | None] = mapped_column(String(80))
    abi: Mapped[str | None] = mapped_column(String(80))
    version_code: Mapped[int | None] = mapped_column(Integer)
    sdk_int: Mapped[int | None] = mapped_column(Integer, index=True)
    dichte_dpi: Mapped[int | None] = mapped_column(Integer)
    einstellungen: Mapped[dict] = mapped_column(JSON, default=dict)
    berechtigungen: Mapped[dict] = mapped_column(JSON, default=dict)
    dienste: Mapped[dict] = mapped_column(JSON, default=dict)
    geraet_details: Mapped[dict] = mapped_column(JSON, default=dict)
    # Rollendes Stundenfenster, serverseitig; nie Teil der Antwort.
    report_zeiten: Mapped[list] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_report_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    fehler: Mapped[list[AppGeraetFehler]] = relationship(cascade="all, delete-orphan")


class AppGeraetFehler(Base):
    __tablename__ = "app_geraet_fehler"
    __table_args__ = (UniqueConstraint("geraet_id", "komponente", "code", name="uq_app_geraet_fehler"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    geraet_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_geraet.id", ondelete="CASCADE"), index=True)
    komponente: Mapped[str] = mapped_column(String(40))
    code: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(String(200))
    anzahl: Mapped[int] = mapped_column(Integer)
    first_at: Mapped[datetime] = mapped_column(DateTime)
    last_at: Mapped[datetime] = mapped_column(DateTime)
    app_version: Mapped[str | None] = mapped_column(String(30))
