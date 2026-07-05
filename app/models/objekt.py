"""Objektverwaltung: einsatzrelevante Objekte (BMA-Objekte, Wohnanlagen, ...).

PR 1: Kernentitaeten Objekt, ObjektKategorie, ObjektZusatzadresse, ObjektBMA,
ObjektChange (feldgenaues Aenderungsprotokoll nach IncidentChange-Muster).
PR 2: GefahrenKatalog/ObjektGefahr, MerkmalKatalog/ObjektMerkmal,
ObjektKontakt (Mehrfach-Telefone als JSON), ObjektWohnanlage.

Alle Tabellen sind TenantScoped (org-isoliert) und in _TENANT_TABLE_NAMES
(app/core/tenant.py) registriert.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
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

# Status-Workflow: Entwurf → Freigegeben ⇄ In Ueberarbeitung → Archiviert
OBJEKT_STATUS_ENTWURF = "entwurf"
OBJEKT_STATUS_FREIGEGEBEN = "freigegeben"
OBJEKT_STATUS_UEBERARBEITUNG = "in_ueberarbeitung"
OBJEKT_STATUS_ARCHIVIERT = "archiviert"

OBJEKT_STATUS_LABELS = {
    OBJEKT_STATUS_ENTWURF: "Entwurf",
    OBJEKT_STATUS_FREIGEGEBEN: "Freigegeben",
    OBJEKT_STATUS_UEBERARBEITUNG: "In Überarbeitung",
    OBJEKT_STATUS_ARCHIVIERT: "Archiviert",
}

# Erlaubte Statusuebergaenge (von → nach)
OBJEKT_STATUS_UEBERGAENGE: dict[str, set[str]] = {
    OBJEKT_STATUS_ENTWURF: {OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_ARCHIVIERT},
    OBJEKT_STATUS_FREIGEGEBEN: {OBJEKT_STATUS_UEBERARBEITUNG, OBJEKT_STATUS_ARCHIVIERT},
    OBJEKT_STATUS_UEBERARBEITUNG: {OBJEKT_STATUS_FREIGEGEBEN, OBJEKT_STATUS_ARCHIVIERT},
    OBJEKT_STATUS_ARCHIVIERT: {OBJEKT_STATUS_UEBERARBEITUNG},
}

# Piktogramm-Typen fuer den Gefahren-Katalog (steuern Chip-/Symbol-Rendering)
GEFAHR_PIKTOGRAMME = {
    "ex": "💥 EX-Bereich",
    "gas": "🔥 Gas",
    "chemie": "🧪 Chemie / Gefahrstoff",
    "hochspannung": "⚡ Hochspannung",
    "pv": "☀️ Photovoltaik",
    "nh3": "❄️ Ammoniak (NH3)",
    "brandlast": "🔥 Hohe Brandlast",
    "sonstig": "⚠️ Sonstige Gefahr",
}

# Kontakt-Arten
KONTAKT_ARTEN = {
    "brandschutzbeauftragter": "Brandschutzbeauftragter",
    "betreiber": "Betreiber",
    "hausverwaltung": "Hausverwaltung",
    "schluesseltraeger": "Schlüsselträger",
    "sonstig": "Sonstiger Kontakt",
}

# Dokumentarten-Taxonomie (fix, aus EUS uebernommen — Entscheidung 2026-07-05)
DOKUMENTARTEN = {
    "bma_datenblatt": "BMA Datenblatt",
    "bma_melderplan": "BMA Melderplan",
    "brandschutzplan": "Brandschutzplan",
    "gefahrgutdatenblatt": "Gefahrgutdatenblatt",
    "lageplan": "Lageplan",
    "objektinformation": "Objektinformation",
}

# Verarbeitungsstatus eines hochgeladenen Dokuments
DOKUMENT_STATUS_NEU = "neu"
DOKUMENT_STATUS_VERARBEITUNG = "verarbeitung"
DOKUMENT_STATUS_FERTIG = "fertig"
DOKUMENT_STATUS_FEHLER = "fehler"

# Symbolkatalog der Objekt-Lagekarte (Code → Label). Das Rendering (Inline-SVG/
# Styling) lebt client-seitig in app/static/js/objekt_karte.js (objektSymbolHtml),
# analog taktSymbolHtml der GSL-Karte.
OBJEKT_SYMBOL_TYPEN = {
    "fsd": "FSD / Schlüsselsafe",
    "schluesselbox": "Schlüsselbox",
    "bsp": "Brandschutzplan (Ablage)",
    "bmz": "BMZ (Brandmelderzentrale)",
    "fbf": "FBF (Feuerwehr-Bedienfeld)",
    "dlk_stellplatz": "Drehleiter-Stellplatz",
    "objektfunk": "Objektfunk-Bedienfeld",
    "sammelplatz": "Sammelplatz",
    "feuerloescher": "Feuerlöscher",
    "hauptzugang": "Hauptzugang",
    "nebenzugang": "Nebenzugang",
    "stiege": "Stiege",
    "aufzug": "Aufzug",
    "gefahr_ex": "Gefahr: EX-Bereich",
    "gefahr_gas": "Gefahr: Gas",
    "gefahr_chemie": "Gefahr: Chemie",
    "gefahr_strom": "Gefahr: Hochspannung",
    "gefahr_pv": "Gefahr: Photovoltaik",
    "hydrant_ueberflur": "Hydrant (Überflur)",
    "hydrant_unterflur": "Hydrant (Unterflur)",
}


class ObjektKategorie(TenantScoped, Base):
    """Objekt-Kategorie je Org (Gewerbe/Industrie, Wohnanlage, ...)."""
    __tablename__ = "objekt_kategorie"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_objekt_kategorie_org_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Objekt(TenantScoped, Base):
    """Kernentitaet: einsatzrelevantes Objekt."""
    __tablename__ = "objekt"
    __table_args__ = (
        UniqueConstraint("org_id", "nummer", name="uq_objekt_org_nummer"),
        Index("ix_objekt_org_name", "org_id", "name"),
        Index("ix_objekt_org_status", "org_id", "status"),
        Index("ix_objekt_org_revision", "org_id", "revision_datum"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    nummer: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vulgoname: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kategorie_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("objekt_kategorie.id", ondelete="SET NULL"), nullable=True
    )
    strasse: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hausnummer: Mapped[str | None] = mapped_column(String(20), nullable=True)
    plz: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ort: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    informationen: Mapped[str | None] = mapped_column(Text, nullable=True)
    anfahrtsweg: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OBJEKT_STATUS_ENTWURF)
    revision_datum: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Sent-Marker fuer Revisions-Erinnerung (Muster verleih_erinnerung)
    revision_erinnert_am: Mapped[date | None] = mapped_column(Date, nullable=True)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    aktualisiert_am: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    erstellt_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    aktualisiert_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    kategorie: Mapped[ObjektKategorie | None] = relationship(lazy="joined")
    bma: Mapped[ObjektBMA | None] = relationship(
        back_populates="objekt", uselist=False, cascade="all, delete-orphan"
    )
    zusatzadressen: Mapped[list[ObjektZusatzadresse]] = relationship(
        back_populates="objekt", cascade="all, delete-orphan",
        order_by="ObjektZusatzadresse.sort",
    )
    gefahren: Mapped[list[ObjektGefahr]] = relationship(
        back_populates="objekt", cascade="all, delete-orphan",
        order_by="ObjektGefahr.sort",
    )
    merkmale: Mapped[list[ObjektMerkmal]] = relationship(
        back_populates="objekt", cascade="all, delete-orphan",
    )
    kontakte: Mapped[list[ObjektKontakt]] = relationship(
        back_populates="objekt", cascade="all, delete-orphan",
        order_by="ObjektKontakt.sort",
    )
    wohnanlage: Mapped[ObjektWohnanlage | None] = relationship(
        back_populates="objekt", uselist=False, cascade="all, delete-orphan",
    )
    karten_objekte: Mapped[list[ObjektKartenObjekt]] = relationship(
        cascade="all, delete-orphan",
        order_by="ObjektKartenObjekt.sort",
    )

    def hat_merkmal(self, code: str) -> bool:
        """True wenn dem Objekt ein Katalog-Merkmal mit diesem Code zugeordnet ist."""
        return any(m.merkmal and m.merkmal.code == code for m in self.merkmale)

    @property
    def anzeige_nummer(self) -> str:
        """Anzeige-Nummernformat, z. B. 'OBJ-0042'."""
        return f"OBJ-{self.nummer:04d}"

    @property
    def adresse_zeile(self) -> str:
        teile = []
        if self.strasse:
            teile.append(f"{self.strasse} {self.hausnummer or ''}".strip())
        ort = f"{self.plz or ''} {self.ort or ''}".strip()
        if ort:
            teile.append(ort)
        return ", ".join(teile)


class ObjektZusatzadresse(TenantScoped, Base):
    """Zusatzadresse/Zugang (Stiegen mit eigener Adresse, Zufahrten)."""
    __tablename__ = "objekt_zusatzadresse"
    __table_args__ = (Index("ix_objekt_zusatzadresse_org_objekt", "org_id", "objekt_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False
    )
    bezeichnung: Mapped[str] = mapped_column(String(100), nullable=False)
    strasse: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hausnummer: Mapped[str | None] = mapped_column(String(20), nullable=True)
    plz: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ort: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    objekt: Mapped[Objekt] = relationship(back_populates="zusatzadressen")


class ObjektBMA(TenantScoped, Base):
    """BMA-Block (1:1 optional): Brandmeldeanlage, FSD/Schluesselsafe."""
    __tablename__ = "objekt_bma"
    __table_args__ = (Index("ix_objekt_bma_org_nummer", "org_id", "bma_nummer"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    bma_nummer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rfl_nummer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bmz_standort: Mapped[str | None] = mapped_column(String(300), nullable=True)
    fbf_standort: Mapped[str | None] = mapped_column(String(300), nullable=True)
    laufkarten_ablageort: Mapped[str | None] = mapped_column(String(300), nullable=True)
    uebertragungseinrichtung: Mapped[str | None] = mapped_column(String(200), nullable=True)
    schluesselsafe_vorhanden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schluesselsafe_standort: Mapped[str | None] = mapped_column(String(300), nullable=True)
    schluesselsafe_inhalt: Mapped[str | None] = mapped_column(String(300), nullable=True)
    benachrichtigung_sms: Mapped[str | None] = mapped_column(String(100), nullable=True)
    benachrichtigung_email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    objekt: Mapped[Objekt] = relationship(back_populates="bma")


class GefahrenKatalog(TenantScoped, Base):
    """Gefahren-Katalog je Org (EX, Gas, Chemie, PV, ...) mit Piktogramm-Typ."""
    __tablename__ = "gefahren_katalog"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_gefahren_katalog_org_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Piktogramm-Typ: ex / gas / chemie / hochspannung / pv / nh3 / brandlast / sonstig
    piktogramm_typ: Mapped[str] = mapped_column(String(30), nullable=False, default="sonstig")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ObjektGefahr(TenantScoped, Base):
    """Strukturierter Gefahren-Eintrag am Objekt (statt Freitext-Blob wie im EUS)."""
    __tablename__ = "objekt_gefahr"
    __table_args__ = (Index("ix_objekt_gefahr_org_objekt", "org_id", "objekt_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False
    )
    gefahr_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("gefahren_katalog.id", ondelete="RESTRICT"), nullable=False
    )
    un_nummer: Mapped[str | None] = mapped_column(String(10), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    objekt: Mapped[Objekt] = relationship(back_populates="gefahren")
    gefahr: Mapped[GefahrenKatalog] = relationship(lazy="joined")


class MerkmalKatalog(TenantScoped, Base):
    """Objektmerkmal-Katalog je Org (Schluesselbox, Brandschutzplan, Tiefgarage, ...)."""
    __tablename__ = "merkmal_katalog"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_merkmal_katalog_org_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    # Stabiler Code fuer Seeds/Badges (z. B. "schluesselbox", "brandschutzplan"); NULL bei Eigenanlagen
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ObjektMerkmal(TenantScoped, Base):
    """Zuordnung Merkmal ↔ Objekt mit optionalem Hinweis (z. B. Standort Schluesselbox)."""
    __tablename__ = "objekt_merkmal"
    __table_args__ = (UniqueConstraint("objekt_id", "merkmal_id", name="uq_objekt_merkmal"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False
    )
    merkmal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("merkmal_katalog.id", ondelete="RESTRICT"), nullable=False
    )
    hinweis: Mapped[str | None] = mapped_column(String(300), nullable=True)

    objekt: Mapped[Objekt] = relationship(back_populates="merkmale")
    merkmal: Mapped[MerkmalKatalog] = relationship(lazy="joined")


class ObjektKontakt(TenantScoped, Base):
    """Ansprechpartner am Objekt (Brandschutzbeauftragter, Betreiber, ...)."""
    __tablename__ = "objekt_kontakt"
    __table_args__ = (Index("ix_objekt_kontakt_org_objekt", "org_id", "objekt_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False
    )
    # Art: brandschutzbeauftragter / betreiber / hausverwaltung / schluesseltraeger / sonstig
    art: Mapped[str] = mapped_column(String(50), nullable=False, default="sonstig")
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    # JSON-Liste von Telefonnummern (UI rendert jede als tel:-Button)
    telefone_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    erreichbarkeit: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    objekt: Mapped[Objekt] = relationship(back_populates="kontakte")

    @property
    def telefone(self) -> list[str]:
        import json as _json
        if not self.telefone_json:
            return []
        try:
            werte = _json.loads(self.telefone_json)
            return [str(w) for w in werte if str(w).strip()]
        except (ValueError, TypeError):
            return []


class ObjektWohnanlage(TenantScoped, Base):
    """Wohnanlagen-Zusatzdaten (1:1 optional)."""
    __tablename__ = "objekt_wohnanlage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    wohneinheiten: Mapped[int | None] = mapped_column(Integer, nullable=True)
    geschosse: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stiegen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hausverwaltung_kontakt_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("objekt_kontakt.id", ondelete="SET NULL"), nullable=True
    )
    # DSGVO: nur einsatztaktische Hinweise, sparsam und sachlich (UI zeigt Dauerhinweis)
    hinweise: Mapped[str | None] = mapped_column(Text, nullable=True)

    objekt: Mapped[Objekt] = relationship(back_populates="wohnanlage")
    hausverwaltung_kontakt: Mapped[ObjektKontakt | None] = relationship(
        foreign_keys=[hausverwaltung_kontakt_id]
    )


class ObjektDokument(TenantScoped, Base):
    """Hochgeladenes Original-PDF; wird im Hintergrund in Seiten zerlegt."""
    __tablename__ = "objekt_dokument"
    __table_args__ = (Index("ix_objekt_dokument_org_objekt", "org_id", "objekt_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False
    )
    dateiname_original: Mapped[str] = mapped_column(String(255), nullable=False)
    # Pfad relativ zu settings.OBJEKT_MEDIA_DIR
    pfad: Mapped[str] = mapped_column(String(500), nullable=False)
    mime: Mapped[str] = mapped_column(String(100), nullable=False, default="application/pdf")
    groesse_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Gesamt reservierter Storage (Original + Einzelseiten + Renderings) fuer Quota-Freigabe
    belegt_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    seitenzahl: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Status: neu / verarbeitung / fertig / fehler
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DOKUMENT_STATUS_NEU)
    fehler_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hochgeladen_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    hochgeladen_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    objekt: Mapped[Objekt] = relationship()
    seiten: Mapped[list[ObjektDokumentSeite]] = relationship(
        back_populates="dokument", cascade="all, delete-orphan",
        order_by="ObjektDokumentSeite.seiten_nr",
    )


class ObjektDokumentSeite(TenantScoped, Base):
    """Einzelne zerlegte PDF-Seite mit Rendering und Klassifizierung."""
    __tablename__ = "objekt_dokument_seite"
    __table_args__ = (
        Index("ix_objekt_seite_org_objekt_art", "org_id", "objekt_id", "dokumentart"),
        Index("ix_objekt_seite_dokument", "org_id", "objekt_id", "dokument_id", "seiten_nr"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    # objekt_id denormalisiert fuer schnelle Filterung ohne Join
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False
    )
    dokument_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt_dokument.id", ondelete="CASCADE"), nullable=False
    )
    seiten_nr: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-basiert
    # Verlustfreie pypdf-Einzelseite (Basis fuer Sammel-PDF/Download)
    einzel_pdf_pfad: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Hi-Res-Rendering (~150 dpi PNG); NULL wenn Poppler nicht verfuegbar
    bild_pfad: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumb_pfad: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Klassifizierung (Dokumentart aus DOKUMENTARTEN)
    dokumentart: Mapped[str | None] = mapped_column(String(30), nullable=True)
    titel: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Melderlinie(n), kommagetrennt — nur manuelle Pflege/Suche (keine Alarmtext-Erkennung)
    melderlinien: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stand: Mapped[date | None] = mapped_column(Date, nullable=True)
    bei_einsatz_drucken: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    klassifiziert_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    klassifiziert_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    dokument: Mapped[ObjektDokument] = relationship(back_populates="seiten")


# Quellen und Status der Einsatz-Verknuepfung
OBJEKT_EINSATZ_QUELLEN = {
    "bma": "BMA-Nummer im Alarmtext",
    "adresse": "Adress-Übereinstimmung",
    "geo": "Geografische Nähe",
    "manuell": "Manuell verknüpft",
}
OBJEKT_EINSATZ_BESTAETIGT = "bestaetigt"
OBJEKT_EINSATZ_VORSCHLAG = "vorschlag"


class ObjektEinsatz(TenantScoped, Base):
    """Verknuepfung Einsatz ↔ Objekt (automatisch via Matching oder manuell)."""
    __tablename__ = "objekt_einsatz"
    __table_args__ = (
        UniqueConstraint("incident_id", "objekt_id", name="uq_objekt_einsatz"),
        Index("ix_objekt_einsatz_org_incident", "org_id", "incident_id"),
        Index("ix_objekt_einsatz_org_objekt_ts", "org_id", "objekt_id", "erstellt_am"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False
    )
    # Quelle: bma / adresse / geo / manuell
    quelle: Mapped[str] = mapped_column(String(20), nullable=False)
    # Status: bestaetigt / vorschlag (Geo-Treffer sind immer Vorschlag)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OBJEKT_EINSATZ_VORSCHLAG)
    distanz_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    bestaetigt_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    objekt: Mapped[Objekt] = relationship()


class ObjektKartenObjekt(TenantScoped, Base):
    """Marker/Geometrie der Objekt-Lagekarte (Symbolcode aus OBJEKT_SYMBOL_TYPEN)."""
    __tablename__ = "objekt_karten_objekt"
    __table_args__ = (Index("ix_objekt_karten_org_objekt", "org_id", "objekt_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False
    )
    typ: Mapped[str] = mapped_column(String(40), nullable=False)
    # Punkt-Symbole: lat/lng; Linien/Flaechen: geometry_json (GeoJSON, Muster Sector.geometry)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


KI_VORSCHLAG_OFFEN = "offen"
KI_VORSCHLAG_UEBERNOMMEN = "uebernommen"
KI_VORSCHLAG_VERWORFEN = "verworfen"


class ObjektSeiteKiVorschlag(TenantScoped, Base):
    """KI-Klassifizierungsvorschlag je Dokumentseite (Review-Queue, nie Auto-Apply)."""
    __tablename__ = "objekt_seite_ki_vorschlag"
    __table_args__ = (Index("ix_objekt_ki_vorschlag_org_status", "org_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    seite_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt_dokument_seite.id", ondelete="CASCADE"), nullable=False
    )
    dokumentart: Mapped[str | None] = mapped_column(String(30), nullable=True)
    titel: Mapped[str | None] = mapped_column(String(200), nullable=True)
    melderlinien: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stand: Mapped[date | None] = mapped_column(Date, nullable=True)
    begruendung: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Status: offen / uebernommen / verworfen
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=KI_VORSCHLAG_OFFEN)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    entschieden_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    entschieden_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    seite: Mapped[ObjektDokumentSeite] = relationship()


class AlarmInfoscreenToken(TenantScoped, Base):
    """Zugangs-Token fuer den oeffentlichen Alarm-Infoscreen (Wandmonitor).

    Muster WeatherDashboardToken: Token wird nur als SHA256-Hash gespeichert
    und beim Anlegen genau einmal im Klartext angezeigt.
    """
    __tablename__ = "alarm_infoscreen_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class ObjektChange(TenantScoped, Base):
    """Feldgenaues Aenderungsprotokoll je Objekt (Muster IncidentChange)."""
    __tablename__ = "objekt_change"
    __table_args__ = (Index("ix_objekt_change_org_objekt_ts", "org_id", "objekt_id", "erstellt_am"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    objekt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    # Bereich: stammdaten / bma / gefahren / kontakte / dokumente / karte / status
    bereich: Mapped[str] = mapped_column(String(50), nullable=False)
    feld: Mapped[str] = mapped_column(String(100), nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
