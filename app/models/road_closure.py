"""Straßensperren und berechnete Einsatz-Anfahrtsrouten."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base

RESTRICTION_TYPES: dict[str, str] = {
    "closed": "Vollsperre",
    "partial": "Teilsperre",
    "construction": "Baustelle",
    "one_way": "Einbahnregelung",
    "weight_limit": "Gewichtsbeschränkung",
    "height_limit": "Höhenbeschränkung",
    "width_limit": "Breitenbeschränkung",
    "residents_only": "Anrainerverkehr",
    "difficult_passage": "Erschwerte Durchfahrt",
    "other": "Sonstige Einschränkung",
}
PRIORITIES: dict[str, str] = {"low": "Niedrig", "normal": "Normal", "high": "Hoch", "critical": "Kritisch"}
GEOMETRY_STATUS: dict[str, str] = {"ok": "Geprüft", "needs_review": "Geometrie prüfen", "missing": "Keine Geometrie"}
CLOSURE_STATUS: dict[str, str] = {
    "planned": "Geplant",
    "active": "Aktiv",
    "expired": "Abgelaufen",
    "cancelled": "Deaktiviert",
}
DIRECTIONS: dict[str, str] = {"both": "Beide Richtungen", "forward": "Hinrichtung", "backward": "Gegenrichtung"}
GEOMETRY_QUALITY: dict[str, str] = {
    "hoch": "Hoch (automatisch, eindeutig)",
    "mittel": "Mittel (automatisch, plausibel)",
    "niedrig": "Niedrig (automatisch, unsicher)",
    "manuell": "Manuell geprüft",
}
ACCESS_TOKEN_ARTEN: dict[str, str] = {"status": "Statusseite", "infoscreen": "Infoscreen", "detail": "Einzelansicht"}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class RoadClosure(TenantScoped, Base):
    __tablename__ = "road_closure"
    __table_args__ = (Index("ix_road_closure_org_valid", "org_id", "valid_from", "valid_until"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    teams_melden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    exceptions: Mapped[str | None] = mapped_column(Text, nullable=True)
    authority: Mapped[str | None] = mapped_column(String(200), nullable=True)
    street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    from_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    to_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    restriction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")
    max_weight_t: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_width_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)
    geometry_status: Mapped[str] = mapped_column(String(20), nullable=False, default="missing")
    geometry_quality: Mapped[str | None] = mapped_column(String(10), nullable=True)
    geometry_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("road_closure.id", ondelete="SET NULL"), nullable=True
    )
    bbox_min_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_min_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_external_update: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_via: Mapped[str] = mapped_column(String(10), nullable=False, default="ui")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    @property
    def restriction_label(self) -> str:
        return RESTRICTION_TYPES.get(self.restriction_type, self.restriction_type)

    @property
    def priority_label(self) -> str:
        return PRIORITIES.get(self.priority, self.priority)

    @property
    def geometry_status_label(self) -> str:
        return GEOMETRY_STATUS.get(self.geometry_status, self.geometry_status)


class RoadClosureShare(Base):
    __tablename__ = "road_closure_share"

    road_closure_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("road_closure.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class RoadClosureChange(TenantScoped, Base):
    __tablename__ = "road_closure_change"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    road_closure_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("road_closure.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    field: Mapped[str | None] = mapped_column(String(60), nullable=True)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="ui")
    mcp_tool: Mapped[str | None] = mapped_column(String(80), nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class RoadClosureDocument(TenantScoped, Base):
    __tablename__ = "road_closure_document"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    road_closure_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("road_closure.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="ui")
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class RoadClosureAccessToken(TenantScoped, Base):
    __tablename__ = "road_closure_access_token"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    art: Mapped[str] = mapped_column(String(12), nullable=False)
    road_closure_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("road_closure.id", ondelete="CASCADE"), nullable=True, index=True
    )
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    berechtigungen_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class RoadClosureTeamsConfig(TenantScoped, Base):
    __tablename__ = "road_closure_teams_config"
    __table_args__ = (UniqueConstraint("org_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    webhook_url_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_neu: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_aenderung: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_aufhebung: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    standard_melden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_map: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )


class RoadClosureNotification(TenantScoped, Base):
    __tablename__ = "road_closure_notification"
    __table_args__ = (
        UniqueConstraint("road_closure_id", "dedup_key"),
        Index("ix_road_closure_notification_due", "org_id", "status", "next_attempt_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    road_closure_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("road_closure.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ereignis: Mapped[str] = mapped_column(String(30), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payload_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="ui")
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IncidentRoute(TenantScoped, Base):
    __tablename__ = "incident_route"
    __table_args__ = (UniqueConstraint("incident_id", "org_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    closure_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)
    route_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    route_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    alternative_status: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    alternative_route_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternative_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    alternative_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    alternative_streets_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    detour_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    detour_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    routing_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    routing_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class IncidentRoadClosure(TenantScoped, Base):
    __tablename__ = "incident_road_closure"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_route_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident_route.id", ondelete="CASCADE"), nullable=False, index=True
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False, index=True
    )
    road_closure_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("road_closure.id", ondelete="SET NULL"), nullable=True
    )
    relevance: Mapped[str] = mapped_column(String(20), nullable=False)
    distance_to_route_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_to_destination_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    title_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    restriction_type_snapshot: Mapped[str | None] = mapped_column(String(30), nullable=True)
    geometry_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    geometry_status_snapshot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
