"""Einsatzcockpit Print & Alarm Gateway (ECPG) – Cloud-Datenmodell.

Lokaler Docker-Container im Feuerwehrhaus, der W&T-Alarm (seriell → Einsatz) und
Netzwerkdruck mit der Cloud verbindet. Muster: SMS-Gateway (SmsGatewayToken +
/ws/sms-gateway), erweitert um eine eigene Gateway-Entitaet mit Pairing.

Entitaeten:
- Gateway  – ein Container je Standort (Pairing per Einmal-Code → Device-Token)
- Printer  – Netzwerkdrucker, je Gateway, aus Discovery/manuell, im Web-UI aktiviert
- PrintJob – ein Druckauftrag (manuell oder aus Druckregel), idempotent
- AlarmIngest – seriell empfangener Alarm (Phase 3), idempotent via raw_hash
- PrintRule  – Automatik-Druckregeln (Phase 4)

Alle Tabellen sind TenantScoped (org-isoliert) und in _TENANT_TABLE_NAMES
(app/core/tenant.py) registriert. Die Gateway-zugewandten Endpunkte (/pair, /ws,
/alarms, Artifact-Download) laufen OHNE User-Tenant-Kontext und filtern explizit
nach der ueber das Device-Token aufgeloesten org_id (Muster SmsGatewayToken).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base

# ── Gateway-Status ─────────────────────────────────────────────────────────────
GATEWAY_STATUS_UNPAIRED = "unpaired"
GATEWAY_STATUS_ONLINE = "online"
GATEWAY_STATUS_OFFLINE = "offline"

GATEWAY_STATUS_LABELS = {
    GATEWAY_STATUS_UNPAIRED: "Nicht gekoppelt",
    GATEWAY_STATUS_ONLINE: "Online",
    GATEWAY_STATUS_OFFLINE: "Offline",
}

# Pairing-Code Gueltigkeit
PAIRING_CODE_TTL_MINUTES = 10

# ── Dokumenttypen (druckbare Artefakte, Cloud-Rendering) ───────────────────────
DOC_EINSATZINFO = "einsatzinfo"
DOC_GSL_LAGEBLATT = "gsl_lageblatt"
DOC_ALARM_ROHTEXT = "alarm_rohtext"
DOC_OBJEKTBLATT = "objektblatt"
DOC_OBJEKT_DOKUMENT = "objekt_dokument"
DOC_AS_PRUEFUNG = "as_pruefung"          # Atemschutzgeräteprüfung (artifact_ref = kommagetrennte IDs)
DOC_TROOP_PROTOKOLL = "troop_protokoll"  # Atemschutztrupp (incident_id + artifact_ref = troop_id)
DOC_TEILNAHME = "teilnahme"              # Teilnehmerliste (artifact_ref = "<bezug_typ>:<bezug_id>[:<sort>]")
DOC_OBJEKT_SAMMEL = "objekt_sammel"      # Objekt-Dokumente Sammelmappe (objekt_id, artifact_ref = art-Filter)
DOC_UAS = "uas"                          # UAS-PDFs (artifact_ref = "<subtyp>:<id>[:<id2>]")
DOC_GSL_JOURNAL = "gsl_journal"          # GSL-Einsatzjournal-Eintrag (gsl_id + artifact_ref = Eintrag-ID)
DOC_VERLEIH_SCHEIN = "verleih_schein"    # Verleihschein (gsl_id = lage_id + artifact_ref = ausleihe_id)
# Reserviert (Station-Rendering steht noch aus – Route-Kontext-Refactor nötig):
# gsl_bericht, mannschaft, qr_einsatz. Diese Drucke laufen bis dahin
# weiterhin nur lokal (unveränderte window.print-/PDF-Ansichten).

DOCUMENT_TYPE_LABELS = {
    DOC_EINSATZINFO: "Einsatzinfo",
    DOC_GSL_LAGEBLATT: "GSL-Lageblatt",
    DOC_ALARM_ROHTEXT: "Alarm-Rohtext",
    DOC_OBJEKTBLATT: "Objektblatt",
    DOC_OBJEKT_DOKUMENT: "Objekt-Dokument",
    DOC_AS_PRUEFUNG: "Atemschutzprüfung",
    DOC_TROOP_PROTOKOLL: "Atemschutz-Protokoll",
    DOC_TEILNAHME: "Teilnehmerliste",
    DOC_OBJEKT_SAMMEL: "Objekt-Sammelmappe",
    DOC_UAS: "Drohnen-Dokument",
    DOC_GSL_JOURNAL: "GSL-Journal",
    DOC_VERLEIH_SCHEIN: "Verleihschein",
}

# ── PrintJob-Status ────────────────────────────────────────────────────────────
JOB_QUEUED = "queued"      # in der Cloud angelegt, noch nicht ans Gateway gesendet
JOB_SENT = "sent"          # ans Gateway uebergeben
JOB_PRINTING = "printing"  # CUPS druckt
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_CANCELED = "canceled"

JOB_STATUS_LABELS = {
    JOB_QUEUED: "In Warteschlange",
    JOB_SENT: "Gesendet",
    JOB_PRINTING: "Druckt",
    JOB_DONE: "Gedruckt",
    JOB_FAILED: "Fehlgeschlagen",
    JOB_CANCELED: "Abgebrochen",
}
JOB_TERMINAL = frozenset({JOB_DONE, JOB_FAILED, JOB_CANCELED})

JOB_SOURCE_MANUAL = "manual"
JOB_SOURCE_RULE = "rule"

# ── Druckregel-Trigger ─────────────────────────────────────────────────────────
TRIGGER_EINSATZ_CREATED = "einsatz_created"
TRIGGER_EINSATZ_UPDATED = "einsatz_updated"
TRIGGER_GSL_CREATED = "gsl_created"
TRIGGER_GSL_LAGE_UPDATED = "gsl_lage_updated"
TRIGGER_ALARM_SERIAL = "alarm_serial_received"
TRIGGER_MANUAL_ONLY = "manual_only"

TRIGGER_LABELS = {
    TRIGGER_EINSATZ_CREATED: "Einsatz angelegt",
    TRIGGER_EINSATZ_UPDATED: "Einsatz aktualisiert",
    TRIGGER_GSL_CREATED: "Großschadenslage angelegt",
    TRIGGER_GSL_LAGE_UPDATED: "GSL-Lage aktualisiert",
    TRIGGER_ALARM_SERIAL: "Serieller Alarm empfangen",
    TRIGGER_MANUAL_ONLY: "Nur manuell",
}

# Objekt-Elemente, die eine Druckregel bei zugeordnetem Objekt mitdrucken kann.
# Schlüssel bilden echte, im Dispatcher auflösbare Objekt-Dokumentseiten ab
# (ObjektDokumentSeite): "bei_einsatz_drucken" = alle so markierten Seiten, alle
# übrigen Schlüssel = ObjektDokumentSeite.dokumentart (siehe DOKUMENTARTEN in
# app/models/objekt.py). So druckt eine Regel bei zugeordnetem Objekt genau diese Seiten.
OBJEKT_ELEMENT_LABELS = {
    "bei_einsatz_drucken": "Seiten „bei Einsatz drucken\"",
    "brandschutzplan": "Brandschutzplan",
    "lageplan": "Lageplan",
    "bma_datenblatt": "BMA Datenblatt",
    "bma_melderplan": "BMA Melderplan",
    "gefahrgutdatenblatt": "Gefahrgutdatenblatt",
    "objektinformation": "Objektinformation",
}

# ── Parse-Status AlarmIngest ───────────────────────────────────────────────────
PARSE_OK = "parsed"
PARSE_FAILED = "parse_failed"


class Gateway(TenantScoped, Base):
    """Ein Gateway-Container je Standort. Gehoert zu genau einer Org."""
    __tablename__ = "gateway"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    standort: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Device-Token (SHA256-Hash, hash_api_key) – gesetzt nach Pairing, rotierbar.
    device_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    # Einmal-Pairing-Code (SHA256-Hash) + Ablauf.
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=GATEWAY_STATUS_UNPAIRED)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # W&T-Verbindungsstatus (aus serial_status-Meldung des Gateways)
    serial_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Marker fuer Offline-Benachrichtigung (verhindert Doppelversand, Muster Sent-Marker)
    offline_alerted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # W&T-Com-Server-Konfiguration: {host, port, datagram_strategy, idle_ms, charset,
    # notfalldruck_printer_id}
    wut_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Parser-Konfiguration: {parser, regex_set, version}
    parser_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    aktualisiert_am: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    printers: Mapped[list[Printer]] = relationship(
        "Printer", back_populates="gateway", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_gateway_org", "org_id"),
    )

    @property
    def status_label(self) -> str:
        return GATEWAY_STATUS_LABELS.get(self.status, self.status)

    @property
    def is_paired(self) -> bool:
        return bool(self.device_token_hash)


class Printer(TenantScoped, Base):
    """Netzwerkdrucker, je Gateway. Aus Discovery vorgeschlagen oder per IP angelegt,
    Aktivierung/Benennung erfolgt im Web-UI (dann richtet das Gateway die CUPS-Queue ein)."""
    __tablename__ = "printer"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gateway_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("gateway.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    modell: Mapped[str | None] = mapped_column(String(150), nullable=True)
    uri: Mapped[str] = mapped_column(String(300), nullable=False)  # ipp://<ip>/ipp/print
    # Identitaet: {serial, mac, uuid, ip} – bevorzugt Serial/UUID, sonst MAC, sonst IP
    identity: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Faehigkeiten: {duplex, color, media[], ...}
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Standard-Optionen: {duplex, color, media}
    defaults: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Live-Status: {reachable, state, toner_pct, paper, checked_at}
    status: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    discovered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    gateway: Mapped[Gateway] = relationship("Gateway", back_populates="printers")

    __table_args__ = (
        Index("ix_printer_org_gateway", "org_id", "gateway_id"),
    )


class PrintJob(TenantScoped, Base):
    """Ein Druckauftrag. idempotency_key garantiert 'max. einmal automatisch je
    (Quelle, Regel, Dokument, Drucker)'."""
    __tablename__ = "print_job"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gateway_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("gateway.id", ondelete="CASCADE"), nullable=False
    )
    printer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("printer.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=JOB_SOURCE_MANUAL)
    rule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("print_rule.id", ondelete="SET NULL"), nullable=True
    )
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="SET NULL"), nullable=True
    )
    # major_incident.id ist Integer (INT), NICHT BigInteger — der FK-Spaltentyp muss exakt
    # passen, sonst MySQL/MariaDB errno 150 ("Foreign key constraint is incorrectly formed").
    gsl_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="SET NULL"), nullable=True
    )
    objekt_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="SET NULL"), nullable=True
    )
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # Interner Render-Key (z. B. Seiten-ID bei objekt_dokument); NICHT die signierte URL.
    artifact_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Druckoptionen: {copies, duplex, color, media}
    options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JOB_QUEUED)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    aktualisiert_am: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_print_job_org_incident", "org_id", "incident_id"),
        Index("ix_print_job_org_gateway_status", "org_id", "gateway_id", "status"),
    )

    @property
    def status_label(self) -> str:
        return JOB_STATUS_LABELS.get(self.status, self.status)

    @property
    def document_label(self) -> str:
        return DOCUMENT_TYPE_LABELS.get(self.document_type, self.document_type)


class AlarmIngest(TenantScoped, Base):
    """Seriell empfangener Alarm (Phase 3). raw_hash macht den Ingest idempotent
    gegen Retries und verhindert Doppel-Einsaetze."""
    __tablename__ = "alarm_ingest"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gateway_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("gateway.id", ondelete="CASCADE"), nullable=False
    )
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    charset: Mapped[str | None] = mapped_column(String(20), nullable=True)
    parsed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parse_status: Mapped[str] = mapped_column(String(20), nullable=False, default=PARSE_OK)
    einsatz_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="SET NULL"), nullable=True
    )
    # created / merged_lis / merged_api
    dedup_action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_alarm_ingest_org_received", "org_id", "received_at"),
    )


class PrintRule(TenantScoped, Base):
    """Automatik-Druckregel (Phase 4). Wird vom PrintDispatcher auf Domain-Events
    ausgewertet."""
    __tablename__ = "print_rule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger: Mapped[str] = mapped_column(String(30), nullable=False)
    # {min_alarmstufe, stichwort[], nur_bma, zeitfenster}
    filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Liste Dokumenttypen (DOC_*)
    documents: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Liste Objekt-Element-Keys (OBJEKT_ELEMENT_LABELS)
    objekt_elements: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Ziel-Drucker (Liste von printer.id)
    printer_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    fallback_printer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("printer.id", ondelete="SET NULL"), nullable=True
    )
    # {copies, duplex, color}
    options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    aktualisiert_am: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_print_rule_org_trigger_aktiv", "org_id", "trigger", "aktiv"),
        UniqueConstraint("org_id", "name", name="uq_print_rule_org_name"),
    )

    @property
    def trigger_label(self) -> str:
        return TRIGGER_LABELS.get(self.trigger, self.trigger)
