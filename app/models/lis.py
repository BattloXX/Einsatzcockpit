"""LIS/IPR-Anbindung: Org-Konfiguration + Dedup-Merker für synchronisierte LIS-Objekte.

Siehe app/services/lis/ für Client, Mapping, Matching und Sync-Orchestrierung.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OrgLisConfig(Base):
    """LIS/IPR-Verbindung einer Organisation (1:1 je Org). Muster: OrgSsoConfig."""
    __tablename__ = "org_lis_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # LIS-Verbindung
    base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    site: Mapped[str] = mapped_column(String(50), nullable=False, default="LIS")
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # LIS-GUID
    # AddSessionEntries-Key "ProjectId" — ohne diesen wirft GetTasks serverseitig eine
    # NullReferenceException (SessionData.get_OrganizationId() bleibt null), siehe
    # lis_client.py::login(). Aus einem echten Referenz-Client-Mitschnitt entnommen,
    # ist server-/installationsweit konstant, nicht pro Operation.
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # Schreibt lokale Fahrzeugstatus-Änderungen (set_unit_status) zurück ins LIS
    # (SetOperationUnitStatus) — abschaltbar, weil dies aktiv in das echte
    # Leitstellensystem schreibt statt nur zu lesen. Default aus, opt-in je Org.
    push_vehicle_status: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Zugangsdaten
    username: Mapped[str | None] = mapped_column(String(150), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-verschlüsselt
    # Wenn gesetzt: password_enc enthält bereits den fertigen SHA1-Hash (nicht das
    # Klartext-Passwort) — manche Betreiber geben nur den Hash heraus. Steuert, ob
    # lis_client.py::login() den gespeicherten Wert noch selbst hasht oder unverändert
    # verwendet. Entweder-oder zum Klartext-Passwort, kein zusätzliches Feld nötig.
    password_is_hash: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Backfill-Steuerung
    last_backfill_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")

    @property
    def is_fully_configured(self) -> bool:
        return bool(self.base_url and self.organization_id and self.username and self.password_enc)


class LisSyncedObject(Base):
    """Dedup-Merker: welche LIS-Objekte (Task-Response, Dokument) wurden bereits verarbeitet.

    Verhindert doppelte Meldungs-/Zu-Absage-/Dokument-Verarbeitung bei wiederholtem Polling.
    """
    __tablename__ = "lis_synced_object"
    __table_args__ = (
        UniqueConstraint("org_id", "obj_type", "lis_id", name="uq_lis_synced_org_type_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    obj_type: Mapped[str] = mapped_column(String(30), nullable=False)  # task_response | document
    lis_id: Mapped[str] = mapped_column(String(64), nullable=False)
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
