from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db import Base

# ── GSL Stab: SKKM-Besetzungsjournal ──────────────────────────────────────────

class GslStaffRole(Base):
    """SKKM-Stabsfunktionskatalog, systemweit + je Org erweiterbar."""
    __tablename__ = "gsl_staff_role"

    id:               Mapped[int] = mapped_column(Integer, primary_key=True)
    code:             Mapped[str] = mapped_column(String(20))
    name:             Mapped[str] = mapped_column(String(80))
    sort_order:       Mapped[int] = mapped_column(Integer, default=0)
    is_required:      Mapped[bool] = mapped_column(Boolean, default=False)
    allows_multiple:  Mapped[bool] = mapped_column(Boolean, default=False)
    org_id:           Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    assignments: Mapped[list[GslStaffAssignment]] = relationship(back_populates="role")


class GslStaffAssignment(Base):
    """Wer hat welche SKKM-Funktion von wann bis wann besetzt (inkl. Ablöse-Kette)."""
    __tablename__ = "gsl_staff_assignment"

    id:             Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id:    Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    role_id:        Mapped[int] = mapped_column(Integer, ForeignKey("gsl_staff_role.id"))
    org_id:         Mapped[int] = mapped_column(BigInteger, index=True)
    member_id:      Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True)
    person_name:    Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_lead:        Mapped[bool] = mapped_column(Boolean, default=True)
    start_at:       Mapped[datetime] = mapped_column(DateTime)
    end_at:         Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    predecessor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("gsl_staff_assignment.id", ondelete="SET NULL"), nullable=True)
    sector_id:      Mapped[int | None] = mapped_column(
        Integer, ForeignKey("site_sector.id", ondelete="SET NULL"), nullable=True)
    note:           Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by:     Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    role:           Mapped[GslStaffRole] = relationship(back_populates="assignments")
    incident:       Mapped[MajorIncident] = relationship(
        back_populates="gsl_staff", foreign_keys="GslStaffAssignment.incident_id")
    predecessor:    Mapped[GslStaffAssignment | None] = relationship(
        foreign_keys=[predecessor_id], remote_side="GslStaffAssignment.id")

    @property
    def display_name(self) -> str:
        return self.person_name or "–"


class _SitePriorityColType(TypeDecorator):
    """Maps SitePriority IntEnum ↔ INTEGER column in the database.

    The migration created this column as Integer, so we bypass SQLAlchemy's
    Enum type (which would try to store the string name and break MySQL strict
    mode).  Instead we store/read raw integers and convert in Python.
    """
    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return int(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return SitePriority(int(value))
        except (ValueError, KeyError):
            return None


class MajorIncidentStatus(enum.StrEnum):
    standby = "standby"
    active  = "active"
    closed  = "closed"    # nur manuell erreichbar


class SitePhase(enum.StrEnum):
    eingegangen = "eingegangen"
    erkundung   = "erkundung"
    bewertet    = "bewertet"
    disponiert  = "disponiert"
    in_arbeit   = "in_arbeit"
    erledigt    = "erledigt"
    abgebrochen = "abgebrochen"


class SitePriority(int, enum.Enum):
    sofort       = 1    # Gefahr Leib/Leben
    dringend     = 2    # Orts-/Dammschutz, drohende Ausweitung
    normal       = 3    # kritische Infrastruktur / Umwelt
    aufschiebbar = 4    # reine Sachwerte


class StaffFunction(enum.StrEnum):
    lageleitung   = "lageleitung"
    s1_personal   = "s1"
    s2_lage       = "s2"
    s3_einsatz    = "s3"
    s4_versorgung = "s4"
    s5_presse     = "s5"
    s6_komm       = "s6"


SITE_PRIORITY_COLOR = {
    SitePriority.sofort:       "red",
    SitePriority.dringend:     "orange",
    SitePriority.normal:       "yellow",
    SitePriority.aufschiebbar: "muted",
}

SITE_PRIORITY_LABEL = {
    SitePriority.sofort:       "Sofort",
    SitePriority.dringend:     "Dringend",
    SitePriority.normal:       "Normal",
    SitePriority.aufschiebbar: "Aufschiebbar",
}

STAFF_FUNCTION_LABEL = {
    StaffFunction.lageleitung:   "Lageleitung",
    StaffFunction.s1_personal:   "S1 – Personal",
    StaffFunction.s2_lage:       "S2 – Lage",
    StaffFunction.s3_einsatz:    "S3 – Einsatz",
    StaffFunction.s4_versorgung: "S4 – Versorgung",
    StaffFunction.s5_presse:     "S5 – Presse",
    StaffFunction.s6_komm:       "S6 – Kommunikation",
}


class MajorIncident(Base):
    __tablename__ = "major_incident"

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id:        Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id"), index=True)
    name:          Mapped[str] = mapped_column(String(160))
    description:   Mapped[str | None] = mapped_column(Text, nullable=True)
    status:        Mapped[MajorIncidentStatus] = mapped_column(
                       Enum(MajorIncidentStatus), default=MajorIncidentStatus.active)
    trigger:       Mapped[str] = mapped_column(String(20), default="manual")  # "manual"|"alarm_auto"
    is_exercise:   Mapped[bool] = mapped_column(Boolean, default=False)
    auto_adopt:    Mapped[bool] = mapped_column(Boolean, default=True)
    public_token:  Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    public_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at:    Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    ended_at:      Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at:    Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC),
                                                    onupdate=lambda: datetime.now(UTC))
    leader_assignment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("gsl_staff_assignment.id", ondelete="SET NULL"), nullable=True)

    sites:          Mapped[list[IncidentSite]] = relationship(
        back_populates="major_incident", cascade="all, delete-orphan")
    sectors:        Mapped[list[Sector]] = relationship(cascade="all, delete-orphan")
    staff:          Mapped[list[StaffAssignment]] = relationship(cascade="all, delete-orphan")
    gsl_staff:      Mapped[list[GslStaffAssignment]] = relationship(
        back_populates="incident", cascade="all, delete-orphan",
        foreign_keys="GslStaffAssignment.incident_id")
    comms:          Mapped[list[CommLogEntry]] = relationship(cascade="all, delete-orphan")
    journal_entries: Mapped[list[LageJournalEntry]] = relationship(cascade="all, delete-orphan")
    einheiten:      Mapped[list[LageEinheit]] = relationship(cascade="all, delete-orphan")
    cross_site_markers: Mapped[list["CrossSiteMarker"]] = relationship(cascade="all, delete-orphan")


class Sector(Base):
    __tablename__ = "site_sector"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    major_incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    name:              Mapped[str] = mapped_column(String(80))
    leader_label:      Mapped[str | None] = mapped_column(String(80), nullable=True)
    color:             Mapped[str | None] = mapped_column(String(7), nullable=True)
    geometry:          Mapped[str | None] = mapped_column(Text, nullable=True)     # GeoJSON Polygon
    sort_order:        Mapped[int] = mapped_column(Integer, default=0)
    leader_assignment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("gsl_staff_assignment.id", ondelete="SET NULL"), nullable=True)


class StaffAssignment(Base):
    __tablename__ = "staff_assignment"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    major_incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    function:          Mapped[StaffFunction] = mapped_column(Enum(StaffFunction))
    user_id:           Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True)
    member_id:         Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id"), nullable=True)
    label:             Mapped[str | None] = mapped_column(String(120), nullable=True)
    assigned_at:       Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    released_at:       Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IncidentSite(Base):
    __tablename__ = "incident_site"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    major_incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    org_id:            Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id"), index=True)
    sector_id:         Mapped[int | None] = mapped_column(
        Integer, ForeignKey("site_sector.id", ondelete="SET NULL"), nullable=True)

    bezeichnung:  Mapped[str] = mapped_column(String(160))
    einsatzgrund: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ort:          Mapped[str | None] = mapped_column(String(120), nullable=True)
    strasse:      Mapped[str | None] = mapped_column(String(120), nullable=True)
    hausnr:       Mapped[str | None] = mapped_column(String(20), nullable=True)
    lat:          Mapped[float | None] = mapped_column(Float, nullable=True)
    lng:          Mapped[float | None] = mapped_column(Float, nullable=True)

    source:       Mapped[str] = mapped_column(String(12), default="manual")  # api|manual|buerger
    external_key: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    alarm_stufe:  Mapped[str | None] = mapped_column(String(8), nullable=True)

    phase:         Mapped[SitePhase] = mapped_column(
        Enum(SitePhase), default=SitePhase.eingegangen, index=True)
    priority:      Mapped[SitePriority | None] = mapped_column(_SitePriorityColType, nullable=True)
    danger_score:  Mapped[int | None] = mapped_column(Integer, nullable=True)
    urgency_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_index:    Mapped[int] = mapped_column(Integer, default=0)
    section_assigned_mode: Mapped[str] = mapped_column(String(8), default="auto")  # auto|manual

    incident_id:  Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="SET NULL"), nullable=True)

    created_at:   Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at:   Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC),
                                                   onupdate=lambda: datetime.now(UTC))
    created_by:   Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True)

    major_incident: Mapped[MajorIncident] = relationship(back_populates="sites")
    resources:   Mapped[list[SiteResourceAssignment]] = relationship(cascade="all, delete-orphan")
    log_entries: Mapped[list[SiteLogEntry]] = relationship(
        cascade="all, delete-orphan", order_by="SiteLogEntry.ts")
    media:       Mapped[list[SiteMedia]] = relationship(cascade="all, delete-orphan")


class SiteResourceAssignment(Base):
    __tablename__ = "site_resource_assignment"

    id:               Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("incident_site.id", ondelete="CASCADE"), index=True)
    resource_type:    Mapped[str] = mapped_column(String(12))  # vehicle|member|free_text
    vehicle_id:       Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("vehicle_master.id", ondelete="SET NULL"), nullable=True)
    member_id:        Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True)
    label:            Mapped[str | None] = mapped_column(String(120), nullable=True)
    assigned_at:      Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    committed_at:     Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at:      Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # No unique constraint — Mehrfachverplanung erlaubt, nur Warnung im UI


class SiteLogEntry(Base):
    __tablename__ = "site_log_entry"

    id:               Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("incident_site.id", ondelete="CASCADE"), index=True)
    ts:               Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    user_id:          Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True)
    author_name:      Mapped[str | None] = mapped_column(String(120), nullable=True)
    kind:             Mapped[str] = mapped_column(String(16), default="note")
    text:             Mapped[str] = mapped_column(Text)


class SiteMedia(Base):
    __tablename__ = "site_media"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_site_id:  Mapped[int] = mapped_column(
        Integer, ForeignKey("incident_site.id", ondelete="CASCADE"), index=True)
    stored_filename:   Mapped[str] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(255))
    media_type:        Mapped[str] = mapped_column(String(12))  # image|pdf|video
    uploaded_at:       Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    uploaded_by:       Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True)
    author_name:       Mapped[str | None] = mapped_column(String(120), nullable=True)
    bytes:             Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    org_id:            Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id"), nullable=True, index=True)


class CommLogEntry(Base):
    __tablename__ = "comm_log_entry"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    major_incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    related_site_id:   Mapped[int | None] = mapped_column(
        Integer, ForeignKey("incident_site.id", ondelete="SET NULL"), nullable=True)
    ts:                Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    direction:         Mapped[str] = mapped_column(String(4))   # in|out|int
    channel:           Mapped[str | None] = mapped_column(String(40), nullable=True)
    partner:           Mapped[str | None] = mapped_column(String(120), nullable=True)
    message:           Mapped[str] = mapped_column(Text)
    is_request:        Mapped[bool] = mapped_column(Boolean, default=False)
    handled:           Mapped[bool] = mapped_column(Boolean, default=False)
    user_id:           Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True)
    author_name:       Mapped[str | None] = mapped_column(String(120), nullable=True)


class CitizenReport(Base):
    __tablename__ = "citizen_report"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    major_incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    reporter_name:     Mapped[str | None] = mapped_column(String(120), nullable=True)
    reporter_contact:  Mapped[str | None] = mapped_column(String(120), nullable=True)
    ort:               Mapped[str | None] = mapped_column(String(120), nullable=True)
    strasse:           Mapped[str | None] = mapped_column(String(160), nullable=True)
    lat:               Mapped[float | None] = mapped_column(Float, nullable=True)
    lng:               Mapped[float | None] = mapped_column(Float, nullable=True)
    description:       Mapped[str] = mapped_column(Text)
    photo_filename:    Mapped[str | None] = mapped_column(String(64), nullable=True)
    status:            Mapped[str] = mapped_column(String(10), default="new")  # new|accepted|rejected
    phone_verified:    Mapped[bool] = mapped_column(Boolean, default=False)
    created_at:        Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    source_ip:         Mapped[str | None] = mapped_column(String(45), nullable=True)
    site_id:           Mapped[int | None] = mapped_column(
        Integer, ForeignKey("incident_site.id", ondelete="SET NULL"), nullable=True)


class LageEinheit(Base):
    """Einheit (Fahrzeug/Gruppe/extern/Material) im Ressourcenpool einer Lage."""
    __tablename__ = "lage_einheit"

    id:              Mapped[int] = mapped_column(Integer, primary_key=True)
    lage_id:         Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    vehicle_id:      Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("vehicle_master.id", ondelete="SET NULL"), nullable=True)
    label:           Mapped[str] = mapped_column(String(120))
    commander_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # angefordert | bereitgestellt | im_einsatz | abgerueckt
    status:          Mapped[str] = mapped_column(String(16), default="bereitgestellt")
    is_from_org:     Mapped[bool] = mapped_column(Boolean, default=False)
    added_at:        Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    # Ressourcentyp + Abschnittszuordnung (§3.1)
    resource_type:      Mapped[str] = mapped_column(String(12), default="fahrzeug")  # fahrzeug|extern|material
    sector_id:          Mapped[int | None] = mapped_column(
        Integer, ForeignKey("site_sector.id", ondelete="SET NULL"), nullable=True)
    incident_site_id:   Mapped[int | None] = mapped_column(
        Integer, ForeignKey("incident_site.id", ondelete="SET NULL"), nullable=True)
    org_name:           Mapped[str | None] = mapped_column(String(120), nullable=True)
    bos:                Mapped[str | None] = mapped_column(String(20), nullable=True)
    qty:                Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit:               Mapped[str | None] = mapped_column(String(20), nullable=True)
    requested_at:       Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    arrived_at:         Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    committed_at:       Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at:        Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    leader_assignment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("lage_einheit_leader.id", ondelete="SET NULL"), nullable=True)

    sector:  Mapped[Sector | None] = relationship(foreign_keys=[sector_id])
    leader:  Mapped[LageEinheitLeader | None] = relationship(
        foreign_keys=[leader_assignment_id], lazy="joined")


class LageEinheitLeader(Base):
    """Einheitsführer-Historie je LageEinheit (Gruppenkommandant-Ebene)."""
    __tablename__ = "lage_einheit_leader"

    id:             Mapped[int] = mapped_column(Integer, primary_key=True)
    einheit_id:     Mapped[int] = mapped_column(
        Integer, ForeignKey("lage_einheit.id", ondelete="CASCADE"), index=True)
    member_id:      Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True)
    person_name:    Mapped[str | None] = mapped_column(String(120), nullable=True)
    start_at:       Mapped[datetime] = mapped_column(DateTime)
    end_at:         Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    predecessor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("lage_einheit_leader.id", ondelete="SET NULL"), nullable=True)
    note:           Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by:     Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    predecessor: Mapped[LageEinheitLeader | None] = relationship(
        foreign_keys=[predecessor_id], remote_side="LageEinheitLeader.id")

    @property
    def display_name(self) -> str:
        return self.person_name or "–"


# ── Lage-Journal ──────────────────────────────────────────────────────────────

JOURNAL_CATEGORIES = {
    "entscheidung": "Entscheidung",
    "anweisung":    "Anweisung/Auftrag",
    "meldung":      "Meldung",
    "lagemeldung":  "Lagemeldung (S2)",
    "sonstiges":    "Sonstiges",
}

JOURNAL_CATEGORY_COLOR = {
    "entscheidung": "purple",
    "anweisung":    "orange",
    "meldung":      "blue",
    "lagemeldung":  "green",
    "sonstiges":    "muted",
}

# Vorlagen je Kategorie (Betreff-Platzhalter + Body-Skelett für Quill)
JOURNAL_TEMPLATES: dict[str, dict] = {
    "meldung": {
        "subject": "Meldung: ",
        "body": "<p><strong>Von:</strong> </p><p><strong>Inhalt:</strong> </p><p><strong>Zeitbezug:</strong> </p><p><strong>Quelle/Zuverlässigkeit:</strong> </p>",
    },
    "anweisung": {
        "subject": "Auftrag: ",
        "body": "<p><strong>An:</strong> </p><p><strong>Auftrag:</strong> </p><p><strong>Frist:</strong> </p><p><strong>Rückmeldung erwartet bis:</strong> </p>",
    },
    "entscheidung": {
        "subject": "Entscheidung: ",
        "body": "<p><strong>Lagebezug:</strong> </p><p><strong>Entscheidung:</strong> </p><p><strong>Begründung:</strong> </p><p><strong>Veranlassung/Folgemaßnahmen:</strong> </p>",
    },
    "lagemeldung": {
        "subject": "Lagebild Stand ",
        "body": "<p><strong>Allgemeine Lage:</strong> </p><p><strong>Eigene Lage (Kräfte):</strong> </p><p><strong>Gefahren/Entwicklung:</strong> </p><p><strong>Maßnahmen:</strong> </p>",
    },
    "sonstiges": {"subject": "", "body": ""},
}


class LageJournalEntry(Base):
    __tablename__ = "lage_journal_entry"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    major_incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    ts:                Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    category:          Mapped[str] = mapped_column(String(20), default="sonstiges")
    text:              Mapped[str] = mapped_column(Text)              # Betreff (Pflichtfeld)
    body_html:         Mapped[str | None] = mapped_column(Text, nullable=True)  # Fließtext (sanitisiert)
    partner_from:      Mapped[str | None] = mapped_column(String(120), nullable=True)  # Von (SKKM)
    partner_to:        Mapped[str | None] = mapped_column(String(120), nullable=True)  # An (SKKM)
    measure:           Mapped[str | None] = mapped_column(String(500), nullable=True)  # Veranlassung (SKKM)
    author_name:       Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_id:           Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True)

    media: Mapped[list["LageJournalMedia"]] = relationship(
        "LageJournalMedia", cascade="all, delete-orphan", lazy="select",
        foreign_keys="LageJournalMedia.journal_entry_id",
    )


class LageJournalMedia(Base):
    __tablename__ = "lage_journal_media"

    id:               Mapped[int] = mapped_column(Integer, primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lage_journal_entry.id", ondelete="CASCADE"), index=True)
    stored_filename:  Mapped[str] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(255))
    media_type:       Mapped[str] = mapped_column(String(12))  # image
    uploaded_at:      Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    uploaded_by:      Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    author_name:      Mapped[str | None] = mapped_column(String(120), nullable=True)
    bytes:            Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    org_id:           Mapped[int | None] = mapped_column(BigInteger, ForeignKey("fire_dept.id"), nullable=True, index=True)


# ── Lage-QR-Token (Schnellzugang per QR-Code) ────────────────────────────────

class LageToken(Base):
    """QR-Code-Zugangstokens für Großschadenslagen – gültig solange Lage aktiv."""
    __tablename__ = "lage_token"

    id:                 Mapped[int] = mapped_column(Integer, primary_key=True)
    lage_id:            Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    token_hash:         Mapped[str] = mapped_column(String(64), unique=True)
    issued_by_user_id:  Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id"))
    revoked_at:         Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at:         Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


# ── Einsatzstellenübergreifende Meldungen ─────────────────────────────────────

CROSS_MARKER_TYPE_LABEL: dict[str, str] = {
    "unterfuehrung_geflutet": "Überflutete Unterführung",
    "strasse_ueberflutet":    "Überflutete Straße",
    "hangrutschung":          "Hangrutschung",
    "mure":                   "Murenabgang",
    "baum_umgestuerzt":       "Umgestürzter Baum",
    "vermurung_objekt":       "Vermurtes/überflutetes Objekt",
    "damm_deich":             "Damm-/Deichgefährdung",
    "bruecke_gesperrt":       "Brücke gesperrt",
    "strasse_gesperrt":       "Straßensperre",
    "stromausfall":           "Stromausfall (Bereich)",
    "pegel_messpunkt":        "Pegel-/Messpunkt",
    "gefahrenstoff":          "Gefahrstoff-/Umweltgefahr",
    "verklausung":            "Verklausung",
    "sonstiges":              "Sonstige Lageinfo",
}

CROSS_MARKER_TYPE_ICON: dict[str, str] = {
    "unterfuehrung_geflutet": "🌊",
    "strasse_ueberflutet":    "💧",
    "hangrutschung":          "⛰️",
    "mure":                   "🪨",
    "baum_umgestuerzt":       "🌲",
    "vermurung_objekt":       "🏚️",
    "damm_deich":             "🧱",
    "bruecke_gesperrt":       "🌉",
    "strasse_gesperrt":       "🚧",
    "stromausfall":           "⚡",
    "pegel_messpunkt":        "📈",
    "gefahrenstoff":          "☣️",
    "verklausung":            "🪵",
    "sonstiges":              "📍",
}

CROSS_MARKER_STATUS_LABEL: dict[str, str] = {
    "unbestaetigt":   "Unbestätigt",
    "aktiv":          "Aktiv / Gefahr",
    "in_bearbeitung": "In Bearbeitung",
    "beobachtung":    "An Dritte / Beobachtung",
    "behoben":        "Behoben / Aufgehoben",
}

CROSS_MARKER_STATUS_COLOR: dict[str, str] = {
    "unbestaetigt":   "#6b7280",
    "aktiv":          "#ef4444",
    "in_bearbeitung": "#f59e0b",
    "beobachtung":    "#60a5fa",
    "behoben":        "#22c55e",
}


class CrossSiteMarker(Base):
    """Einsatzstellenübergreifende Meldung/Lageinfo."""
    __tablename__ = "cross_site_marker"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    major_incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="CASCADE"), index=True)
    org_id:            Mapped[int | None] = mapped_column(BigInteger, ForeignKey("fire_dept.id"), nullable=True)
    title:             Mapped[str] = mapped_column(String(160))
    marker_type:       Mapped[str] = mapped_column(String(32), default="sonstiges")
    status:            Mapped[str] = mapped_column(String(16), default="aktiv")
    description:       Mapped[str | None] = mapped_column(Text, nullable=True)
    ort:               Mapped[str | None] = mapped_column(String(120), nullable=True)
    strasse:           Mapped[str | None] = mapped_column(String(160), nullable=True)
    hausnr:            Mapped[str | None] = mapped_column(String(20),  nullable=True)
    lat:               Mapped[float | None] = mapped_column(Float, nullable=True)
    lng:               Mapped[float | None] = mapped_column(Float, nullable=True)
    source:            Mapped[str] = mapped_column(String(12), default="manual")
    sort_index:        Mapped[int] = mapped_column(Integer, default=0)
    created_at:        Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at:        Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC),
                                                        onupdate=lambda: datetime.now(UTC))
    created_by:        Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    author_name:       Mapped[str | None] = mapped_column(String(120), nullable=True)

    @property
    def type_icon(self) -> str:
        return CROSS_MARKER_TYPE_ICON.get(self.marker_type, "📍")

    @property
    def type_label(self) -> str:
        return CROSS_MARKER_TYPE_LABEL.get(self.marker_type, self.marker_type)

    @property
    def status_label(self) -> str:
        return CROSS_MARKER_STATUS_LABEL.get(self.status, self.status)

    @property
    def status_color(self) -> str:
        return CROSS_MARKER_STATUS_COLOR.get(self.status, "#6b7280")

    log_entries: Mapped[list["CrossMarkerLogEntry"]] = relationship(
        cascade="all, delete-orphan", order_by="CrossMarkerLogEntry.ts")
    media: Mapped[list["CrossMarkerMedia"]] = relationship(cascade="all, delete-orphan")

    @property
    def address_line(self) -> str:
        parts = []
        if self.strasse:
            parts.append(self.strasse + (" " + self.hausnr if self.hausnr else ""))
        if self.ort:
            parts.append(self.ort)
        return ", ".join(parts) if parts else ""


class CrossMarkerLogEntry(Base):
    """Notizen / Statusmeldungen zu einer übergreifenden Meldung."""
    __tablename__ = "cross_marker_log_entry"

    id:          Mapped[int] = mapped_column(Integer, primary_key=True)
    marker_id:   Mapped[int] = mapped_column(
        Integer, ForeignKey("cross_site_marker.id", ondelete="CASCADE"), index=True)
    ts:          Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    user_id:     Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    text:        Mapped[str] = mapped_column(Text)


class CrossMarkerMedia(Base):
    """Bilder zu einer übergreifenden Meldung."""
    __tablename__ = "cross_marker_media"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    marker_id:         Mapped[int] = mapped_column(
        Integer, ForeignKey("cross_site_marker.id", ondelete="CASCADE"), index=True)
    stored_filename:   Mapped[str] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(255))
    media_type:        Mapped[str] = mapped_column(String(12))  # image
    uploaded_at:       Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    uploaded_by:       Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    author_name:       Mapped[str | None] = mapped_column(String(120), nullable=True)
    bytes:             Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    org_id:            Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id"), nullable=True, index=True)


# ── Fahrzeugpositions-Historie ─────────────────────────────────────────────────

class VehiclePosition(Base):
    """GPS- oder manuell erfasste Fahrzeugpositionen (Positionshistorie)."""
    __tablename__ = "vehicle_position"

    id:             Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id:    Mapped[int | None] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="SET NULL"), nullable=True, index=True)
    org_id:         Mapped[int] = mapped_column(BigInteger, index=True)
    vehicle_id:     Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("vehicle_master.id", ondelete="SET NULL"), nullable=True, index=True)
    resource_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lat:            Mapped[float] = mapped_column(Float)
    lon:            Mapped[float] = mapped_column(Float)
    accuracy_m:     Mapped[float | None] = mapped_column(Float, nullable=True)
    source:         Mapped[str] = mapped_column(String(8), default="gps")   # gps|manual
    recorded_at:    Mapped[datetime] = mapped_column(DateTime)
    received_at:    Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    reported_by:    Mapped[int | None] = mapped_column(BigInteger, nullable=True)
