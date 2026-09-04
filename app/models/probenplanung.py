"""Datenmodell der Probenplanung.

Die Tabellen sind bereits vollstaendig angelegt; fachliche Router fuer Checklisten,
Medien und Nachbereitung folgen in spaeteren Phasen.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base


class TerminStatus(enum.StrEnum):
    entwurf = "entwurf"
    geplant = "geplant"
    in_vorbereitung = "in_vorbereitung"
    vorbereitung_abgeschlossen = "vorbereitung_abgeschlossen"
    durchfuehrung_laeuft = "durchfuehrung_laeuft"
    durchgefuehrt = "durchgefuehrt"
    abgeschlossen = "abgeschlossen"
    abgesagt = "abgesagt"


class ProbeartTerminTyp(enum.StrEnum):
    uebung = "uebung"
    veranstaltung = "veranstaltung"


class ChecklistItemTyp(enum.StrEnum):
    checkbox = "checkbox"
    ja_nein = "ja_nein"
    text = "text"
    langtext = "langtext"
    datum = "datum"
    uhrzeit = "uhrzeit"
    person = "person"
    auswahl = "auswahl"
    mehrfachauswahl = "mehrfachauswahl"
    datei = "datei"
    bild = "bild"
    link = "link"


class Probeart(TenantScoped, Base):
    __tablename__ = "probeart"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_probeart_org_name"),
        Index("ix_probeart_org_sortierung", "org_id", "sortierung"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    kurz: Mapped[str] = mapped_column(String(20), nullable=False)
    farbe: Mapped[str] = mapped_column(String(7), nullable=False, default="#2563eb")
    sortierung: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    standarddauer_minuten: Mapped[int | None] = mapped_column(Integer)
    druckgruppe: Mapped[str | None] = mapped_column(String(50))
    termin_typ: Mapped[str] = mapped_column(String(20), nullable=False, default=ProbeartTerminTyp.uebung.value)
    checkliste_erforderlich: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checklist_template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("checklist_template.id", ondelete="SET NULL")
    )
    teilnahme_erforderlich: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    nachbereitung_erforderlich: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uebungseinsatz_erlaubt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ChecklistTemplate(TenantScoped, Base):
    __tablename__ = "checklist_template"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_checklist_template_org_name"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    beschreibung: Mapped[str | None] = mapped_column(Text)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    aktive_version_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "checklist_template_version.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_checklist_template_aktive_version",
        ),
    )
    erstellt_von: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))


class ChecklistTemplateVersion(TenantScoped, Base):
    __tablename__ = "checklist_template_version"
    __table_args__ = (UniqueConstraint("org_id", "template_id", "version", name="uq_checklist_template_version"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("checklist_template.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    veroeffentlicht_am: Mapped[datetime | None] = mapped_column(DateTime)
    notiz: Mapped[str | None] = mapped_column(Text)
    erstellt_von: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))


class ChecklistTemplateSection(TenantScoped, Base):
    __tablename__ = "checklist_template_section"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("checklist_template_version.id", ondelete="CASCADE"), nullable=False
    )
    titel: Mapped[str] = mapped_column(String(200), nullable=False)
    beschreibung: Mapped[str | None] = mapped_column(Text)
    sortierung: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChecklistTemplateItem(TenantScoped, Base):
    __tablename__ = "checklist_template_item"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    section_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("checklist_template_section.id", ondelete="CASCADE"), nullable=False
    )
    titel: Mapped[str] = mapped_column(String(200), nullable=False)
    hilfetext: Mapped[str | None] = mapped_column(Text)
    typ: Mapped[str] = mapped_column(String(20), nullable=False, default=ChecklistItemTyp.checkbox.value)
    optionen: Mapped[str | None] = mapped_column(Text)
    pflicht: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sortierung: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    default_verantwortlich_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL")
    )
    faellig_tage_vorher: Mapped[int | None] = mapped_column(Integer)


class ProbeCheckliste(TenantScoped, Base):
    __tablename__ = "probe_checkliste"
    __table_args__ = (UniqueConstraint("org_id", "termin_id", name="uq_probe_checkliste_termin"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    termin_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("termin.id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("checklist_template.id", ondelete="SET NULL")
    )
    template_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("checklist_template_version.id", ondelete="SET NULL")
    )
    template_name: Mapped[str] = mapped_column(String(150), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    items: Mapped[list[ProbeChecklistItem]] = relationship(
        "ProbeChecklistItem", cascade="all, delete-orphan", order_by="ProbeChecklistItem.sortierung"
    )


class ProbeChecklistSection(TenantScoped, Base):
    __tablename__ = "probe_checklist_section"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    checkliste_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("probe_checkliste.id", ondelete="CASCADE"), nullable=False
    )
    titel: Mapped[str] = mapped_column(String(200), nullable=False)
    sortierung: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProbeMedia(TenantScoped, Base):
    __tablename__ = "probe_media"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    termin_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("termin.id", ondelete="CASCADE"), nullable=False)
    art: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    beschreibung: Mapped[str | None] = mapped_column(Text)
    typ: Mapped[str | None] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    thumb_path: Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    hochgeladen_von: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    hochgeladen_am: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))


class ProbeChecklistItem(TenantScoped, Base):
    __tablename__ = "probe_checklist_item"
    __table_args__ = (
        Index("ix_probe_checklist_item_org_checkliste_sort", "org_id", "checkliste_id", "sortierung"),
        Index("ix_probe_checklist_item_org_faellig_zustand", "org_id", "faellig_am", "zustand"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    checkliste_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("probe_checkliste.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("probe_checklist_section.id", ondelete="SET NULL")
    )
    quelle: Mapped[str] = mapped_column(String(20), nullable=False, default="vorlage")
    template_item_id: Mapped[int | None] = mapped_column(BigInteger)
    titel: Mapped[str] = mapped_column(String(200), nullable=False)
    hilfetext: Mapped[str | None] = mapped_column(Text)
    typ: Mapped[str] = mapped_column(String(20), nullable=False)
    optionen: Mapped[str | None] = mapped_column(Text)
    pflicht: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sortierung: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verantwortlich_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL")
    )
    faellig_am: Mapped[date | None] = mapped_column(Date)
    zustand: Mapped[str] = mapped_column(String(20), nullable=False, default="offen")
    begruendung: Mapped[str | None] = mapped_column(Text)
    wert_text: Mapped[str | None] = mapped_column(Text)
    wert_member_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("member.id", ondelete="SET NULL"))
    wert_media_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("probe_media.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    erledigt_von: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    erledigt_am: Mapped[datetime | None] = mapped_column(DateTime)
    aktualisiert_von: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    aktualisiert_am: Mapped[datetime | None] = mapped_column(DateTime)


class ProbePublicToken(TenantScoped, Base):
    __tablename__ = "probe_public_token"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    art: Mapped[str] = mapped_column(String(20), nullable=False)
    termin_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("termin.id", ondelete="CASCADE"))
    jahr: Mapped[int | None] = mapped_column(Integer)
    filter_probeart_ids: Mapped[str | None] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    bezeichnung: Mapped[str | None] = mapped_column(String(150))
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    widerrufen_am: Mapped[datetime | None] = mapped_column(DateTime)
    zuletzt_genutzt_am: Mapped[datetime | None] = mapped_column(DateTime)

    @property
    def is_active(self) -> bool:
        return self.widerrufen_am is None


class ProbeNachbereitung(TenantScoped, Base):
    __tablename__ = "probe_nachbereitung"
    __table_args__ = (UniqueConstraint("org_id", "termin_id", name="uq_probe_nachbereitung_termin"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    termin_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("termin.id", ondelete="CASCADE"), nullable=False)
    bemerkungen: Mapped[str | None] = mapped_column(Text)
    was_lief_gut: Mapped[str | None] = mapped_column(Text)
    verbesserungen: Mapped[str | None] = mapped_column(Text)
    teilnehmer_vollstaendig: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    abgeschlossen_von: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    abgeschlossen_am: Mapped[datetime | None] = mapped_column(DateTime)


class ProbeErkenntnis(TenantScoped, Base):
    __tablename__ = "probe_erkenntnis"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    termin_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("termin.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    kategorie: Mapped[str] = mapped_column(String(30), nullable=False)
    massnahme_text: Mapped[str | None] = mapped_column(Text)
    massnahme_erledigt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sortierung: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProbeChange(TenantScoped, Base):
    __tablename__ = "probe_change"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    termin_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("termin.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    bereich: Mapped[str | None] = mapped_column(String(30))
    feld: Mapped[str | None] = mapped_column(String(60))
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    ip: Mapped[str | None] = mapped_column(String(45))
