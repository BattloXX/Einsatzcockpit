"""Teams-Alarmierung: Konfiguration je Org, optionale Bot-Erweiterung (Zusage/Absage),
Kanalbindungen und gesendete Karten.

Zweistufiges Modell (siehe Wiki-Doku "Administration-Teams-Alarmierung"):
- Basis-Modus: einfacher Teams-„Incoming Webhook"-Connector (kein Azure nötig).
- Bot-Erweiterung (separater Schalter `bot_enabled`): echter Teams-Bot für interaktive
  Zusage-/Absage-Buttons — pro Ziel (Echtalarm/Übung) automatisch bevorzugt, sofern eine
  Kanalbindung existiert; sonst fällt der jeweilige Ziel-Typ auf den Webhook zurück.

Siehe app/services/teams_alarm_service.py (Dispatch-Entscheidung), teams_bot_service.py
(Bot-Versand), teams_bot_auth.py (eingehende Bot-Framework-JWT-Validierung).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Ziel einer Teams-Alarmkarte: Echtalarm oder Übung — je eigener Webhook/Kanalbindung
_TARGET_ENUM = Enum("alarm", "uebung", name="teams_alarm_target")


class TeamsAlarmConfig(Base):
    """Teams-Alarmierungs-Konfiguration einer Organisation (1:1 je Org). Muster: OrgLisConfig."""
    __tablename__ = "teams_alarm_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # Master-Schalter: gesamte Teams-Alarmierung an/aus. Wenn False, passiert für diese
    # Org weder Webhook- noch Bot-Versand.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Übungseinsätze zusätzlich an Teams senden (gleiche Konvention wie
    # OrgSettings.einsatzinfo_sms_send_exercise).
    send_exercise: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Einzeln schaltbare Karteninhalte
    include_map: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_gmaps_link: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_qr_link: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Direktlink zum (login-pflichtigen) Einsatz-Board, analog Web-Push-URL (`/einsatz/{id}`)
    include_board_link: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Basis-Modus: normaler Teams-Kanal-Webhook je Ziel, kein Azure/Bot nötig
    webhook_url_alarm: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    webhook_url_uebung: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Bot-Erweiterung: separater Schalter, unabhängig vom Master-Schalter aktivierbar.
    # Ohne bot_enabled (oder ohne gebundenen Kanal für ein Ziel) wird automatisch der
    # Webhook für dieses Ziel verwendet — kein Hard-Fail.
    bot_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bot_app_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bot_tenant_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bot_client_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")

    @property
    def bot_fully_configured(self) -> bool:
        return bool(self.bot_app_id and self.bot_tenant_id and self.bot_client_secret_enc)


class TeamsChannelBinding(Base):
    """Konversations-Referenz eines per Bot gebundenen Teams-Kanals — nur relevant, wenn
    TeamsAlarmConfig.bot_enabled aktiv ist. Wird beim `conversationUpdate`-Event (Bot wird
    dem Team/Kanal hinzugefügt) automatisch eingefangen; die Zuordnung zu einem Ziel
    (Echtalarm/Übung) erfolgt danach durch den Admin."""
    __tablename__ = "teams_channel_binding"
    __table_args__ = (
        UniqueConstraint("org_id", "target", name="uq_teams_binding_org_target"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    target: Mapped[str] = mapped_column(_TARGET_ENUM, nullable=False)

    service_url: Mapped[str] = mapped_column(String(300), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(300), nullable=False)
    team_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    bot_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    channel_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    captured_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class TeamsCardPost(Base):
    """Protokoll gesendeter Teams-Alarmkarten je Einsatz — für den In-Place-Refresh bei
    Zusage/Absage (Universal Actions) und zur Fehlersuche/Idempotenz."""
    __tablename__ = "teams_card_post"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target: Mapped[str] = mapped_column(_TARGET_ENUM, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(300), nullable=False)
    activity_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class AlarmToken(Base):
    """Anonymer Read-Only-Token für die öffentliche No-Login-Alarmübersicht
    (`/alarm/{token}`) und das Kartenbild (`/api/v1/teams/map/{token}.png`).

    Muster: LagekarteToken — sha256-Hash, ein Token je Einsatz, automatisch erzeugt in
    create_incident() und revoked in close_incident(). Bewusst getrennt von IncidentToken
    (das bindet an einen echten Account) und von LagekarteToken (anderer Scope: GeoJSON-Feed).
    """
    __tablename__ = "alarm_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    incident: Mapped[object] = relationship("Incident", foreign_keys=[incident_id])

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
