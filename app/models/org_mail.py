"""Mail-Versand je Organisation: eigener SMTP-Server + Office 365 / Microsoft Graph.

Fallback-Kette (siehe app/services/mail_service.py::deliver()): Office 365 (falls
aktiviert + vollständig konfiguriert) → eigener SMTP-Server der Org (falls
konfiguriert) → globaler SMTP (SystemSettings, unverändert, letzte Stufe).

Muster: OrgLisConfig (app/models/lis.py) — 1:1-Config je Org, verschlüsseltes
Secret (Fernet, app/core/crypto.py), enabled-Toggle, is_fully_configured-Property.

Die Felder `imap_*`/`read_enabled` bereiten den künftigen Abruf eingehender Mails
aus denselben Postfächern NUR vor (Schema + Admin-UI) — das tatsächliche
Abholen/Parsen/Verarbeiten ist bewusst noch nicht implementiert.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OrgSmtpConfig(Base):
    """Eigener SMTP-Server einer Organisation (1:1 je Org). Ohne diese Zeile bzw.
    ohne enabled=True/is_fully_configured bleibt der globale SMTP (SystemSettings)
    die Fallback-Quelle — siehe mail_service.py::_org_smtp_cfg()."""
    __tablename__ = "org_smtp_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-verschlüsselt
    from_addr: Mapped[str | None] = mapped_column(String(255), nullable=True)
    starttls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=15)

    # ── Vorbereitung Posteingang (KEINE Verarbeitung implementiert) ──────────────
    # Nutzt bewusst dieselben user/password_enc-Felder wie der SMTP-Versand
    # (übliche Annahme: gleiches Postfach für Ein- und Ausgang).
    imap_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    imap_use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")

    @property
    def is_fully_configured(self) -> bool:
        return bool(self.host and self.user and self.password_enc and self.from_addr)


class OrgO365MailConfig(Base):
    """Office 365 / Microsoft Graph (App-only, Client-Credentials) einer Organisation
    (1:1 je Org). Braucht eine Azure-AD-App-Registrierung mit Application permission
    Mail.Send (admin-consented) — siehe Hinweistext in settings_org_mail.html."""
    __tablename__ = "org_o365_mail_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    tenant_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    client_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-verschlüsselt
    sender_address: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # ── Vorbereitung Posteingang (KEINE Verarbeitung implementiert) ──────────────
    # Reine Absichts-Flag: Senden und (künftiges) Lesen nutzen dieselbe
    # App-Registrierung (tenant_id/client_id/client_secret_enc/sender_address),
    # brauchen zusätzlich die Application-Permission Mail.Read in Azure.
    read_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")

    @property
    def is_fully_configured(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret_enc and self.sender_address)
