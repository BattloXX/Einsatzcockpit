"""UAS-Modul Datenmodelle (PR 1–3).

Alle Tabellen sind org-scoped (org_id NOT NULL FK → fire_dept).
Tenant-Scoping läuft über den SQLAlchemy-Session-Event-Listener (TenantScoped-Mixin).
UAS-Tabellen werden manuell in _TENANT_TABLE_NAMES eingetragen (app/core/tenant.py).
"""
from __future__ import annotations

import enum
import secrets
from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base


# ── Enums ─────────────────────────────────────────────────────────────────────

class UASDeviceCeKlasse(str, enum.Enum):
    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    keine = "keine"  # privat/unbemustert


class UASDeviceUnterkategorie(str, enum.Enum):
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"


class UASDeviceStatus(str, enum.Enum):
    aktiv = "aktiv"
    wartung = "wartung"
    ausgemustert = "ausgemustert"


class UASBosStufe(str, enum.Enum):
    stufe_0 = "0"  # keine BOS-Ausbildung
    stufe_1 = "1"
    stufe_2 = "2"


class UASWartungArt(str, enum.Enum):
    monatliche_sichtkontrolle = "monatliche_sichtkontrolle"
    jahresservice = "jahresservice"
    reparatur = "reparatur"


class UASWartungErgebnis(str, enum.Enum):
    io = "io"    # in Ordnung
    nio = "nio"  # nicht in Ordnung


class UASFlugbewegungArt(str, enum.Enum):
    einsatz = "einsatz"
    ausbildung = "ausbildung"
    check = "check"


# ── Geräteregister ────────────────────────────────────────────────────────────

class UASDevice(TenantScoped, Base):
    """Geräteregister: ein Drohnen-/UAS-Gerät je Datensatz (RL 4.1, Anh. 8.9)."""
    __tablename__ = "uas_device"
    __table_args__ = (
        Index("ix_uas_device_org_status", "org_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped

    # Gerätedaten
    bezeichnung: Mapped[str] = mapped_column(String(150), nullable=False)
    hersteller: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    typ: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    # Registrierung & Kennzeichnung (RL 4.1)
    registriernummer: Mapped[str | None] = mapped_column(String(100), nullable=True)  # eID/Betreiber-Nr.

    # Klassifizierung (EASA VO 2019/945, Anh. 8.9)
    ce_klasse: Mapped[str] = mapped_column(
        String(10), nullable=False, default=UASDeviceCeKlasse.C2.value
    )
    unterkategorie: Mapped[str] = mapped_column(
        String(5), nullable=False, default=UASDeviceUnterkategorie.A2.value
    )
    mtom_g: Mapped[int | None] = mapped_column(Integer, nullable=True)        # Startmasse in Gramm
    leergewicht_g: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Ausstattung
    hat_waermebildkamera: Mapped[bool] = mapped_column(Boolean, default=False)
    allwettertauglich: Mapped[bool] = mapped_column(Boolean, default=False)

    # Versicherung (RL 4.8)
    versicherung_polizze: Mapped[str | None] = mapped_column(String(100), nullable=True)
    versicherung_gueltig_bis: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Betrieb
    sybos_id: Mapped[str | None] = mapped_column(String(50), nullable=True)   # (RL 7.6)
    beschaffungsdatum: Mapped[date | None] = mapped_column(Date, nullable=True)
    tauschintervall_jahre: Mapped[int] = mapped_column(Integer, default=7)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UASDeviceStatus.aktiv.value
    )

    # QR-Deep-Link-Token (für Wartungsbuch-Scan)
    qr_token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True,
        default=lambda: secrets.token_urlsafe(32)
    )

    notizen: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    wartungen: Mapped[list[UASWartung]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    flugbewegungen: Mapped[list[UASFlugbewegung]] = relationship(
        back_populates="device"
    )


# ── Piloten & Zertifikate ──────────────────────────────────────────────────────

class UASPilot(TenantScoped, Base):
    """Pilot / Zertifikat-Träger (RL 4.1, 5.2–5.7, Anh. 8.6–8.8)."""
    __tablename__ = "uas_pilot"
    __table_args__ = (
        Index("ix_uas_pilot_org_aktiv", "org_id", "aktiv"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped

    # Verknüpfung zur Mitgliedertabelle (nullable für externe Helfer)
    person_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True
    )

    # Stammdaten
    nachname: Mapped[str] = mapped_column(String(100), nullable=False)
    vorname: Mapped[str] = mapped_column(String(100), nullable=False)
    geburtsdatum: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Voraussetzungen (RL 4.1)
    ist_truppfuehrer: Mapped[bool] = mapped_column(Boolean, default=False)

    # EASA-Zertifikate (Anh. 8.6/8.7)
    a1a3_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    a1a3_gueltig_bis: Mapped[date | None] = mapped_column(Date, nullable=True)
    a2_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    a2_gueltig_bis: Mapped[date | None] = mapped_column(Date, nullable=True)

    # BOS-Ausbildung (Anh. 8.8)
    bos_stufe: Mapped[str] = mapped_column(
        String(5), nullable=False, default=UASBosStufe.stufe_0.value
    )
    bos_ausbildung_datum: Mapped[date | None] = mapped_column(Date, nullable=True)
    bos_rezert_bis: Mapped[date | None] = mapped_column(Date, nullable=True)   # alle 5 Jahre

    # LFV-Zulassung (RL 4.1)
    lfv_zugelassen: Mapped[bool] = mapped_column(Boolean, default=False)

    # Rollen innerhalb des Teams (JSON: {"teamleiter": true, "pilot": true, "operator": false})
    qualifikationen: Mapped[str | None] = mapped_column(Text, nullable=True)

    aktiv: Mapped[bool] = mapped_column(Boolean, default=True)
    notizen: Mapped[str | None] = mapped_column(Text, nullable=True)

    flugbewegungen: Mapped[list[UASFlugbewegung]] = relationship(
        back_populates="pilot"
    )


# ── Flugbewegungen (Currency) ─────────────────────────────────────────────────

class UASFlugbewegung(TenantScoped, Base):
    """Je Flugbewegung eine Zeile – Grundlage für Currency-Prüfung (3 in 90 Tagen, RL 4.1).

    Wird automatisch beim Abschluss eines uas_flug-Datensatzes erstellt (PR 4).
    Kann auch manuell für Ausbildungsflüge vor Systemeinführung eingetragen werden.
    """
    __tablename__ = "uas_flugbewegung"
    __table_args__ = (
        Index("ix_uas_flugbewegung_pilot_datum", "pilot_id", "datum"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped

    pilot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("uas_pilot.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("uas_device.id", ondelete="SET NULL"), nullable=True
    )
    datum: Mapped[date] = mapped_column(Date, nullable=False)
    dauer_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    art: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UASFlugbewegungArt.einsatz.value
    )
    # Rückverweis auf den Flugbuch-Eintrag (nullable: manuelle Einträge haben keinen)
    uas_flug_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True   # FK zu uas_flug (PR 4), ohne DB-Constraint bis PR 4
    )

    pilot: Mapped[UASPilot] = relationship(back_populates="flugbewegungen")
    device: Mapped[UASDevice | None] = relationship(back_populates="flugbewegungen")


# ── Wartungsbuch ──────────────────────────────────────────────────────────────

class UASWartung(TenantScoped, Base):
    """Wartungsbuch-Eintrag je Gerät (RL Anh. 8.5)."""
    __tablename__ = "uas_wartung"
    __table_args__ = (
        Index("ix_uas_wartung_device_datum", "device_id", "datum"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped

    device_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("uas_device.id", ondelete="CASCADE"), nullable=False
    )
    datum: Mapped[date] = mapped_column(Date, nullable=False)
    art: Mapped[str] = mapped_column(
        String(40), nullable=False,
        default=UASWartungArt.monatliche_sichtkontrolle.value
    )
    # Prüfpunkte als JSON-Liste: [{"key": "...", "label": "...", "erledigt": true, "bemerkung": ""}]
    pruefpunkte: Mapped[str | None] = mapped_column(Text, nullable=True)
    pruefer: Mapped[str | None] = mapped_column(String(150), nullable=True)
    ergebnis: Mapped[str] = mapped_column(
        String(5), nullable=False, default=UASWartungErgebnis.io.value
    )
    bemerkung: Mapped[str | None] = mapped_column(Text, nullable=True)
    naechste_faellig: Mapped[date | None] = mapped_column(Date, nullable=True)

    device: Mapped[UASDevice] = relationship(back_populates="wartungen")


# ── Drohnen-Einsatz (PR 3) ────────────────────────────────────────────────────

class UASEinsatzStatus(str, enum.Enum):
    alarmiert = "alarmiert"
    angemeldet = "angemeldet"
    im_einsatz = "im_einsatz"
    abgemeldet = "abgemeldet"
    abgeschlossen = "abgeschlossen"


class UASEinsatzRolle(str, enum.Enum):
    teamleiter = "teamleiter"
    pilot = "pilot"
    operator = "operator"
    luftraumbeobachter = "luftraumbeobachter"
    versorger = "versorger"
    bildschirmbeobachter = "bildschirmbeobachter"
    geraetewart = "geraetewart"


class UASEinsatz(TenantScoped, Base):
    """Drohneneinsatz je Incident (1:1). Status-Lebenszyklus: alarmiert → abgeschlossen."""
    __tablename__ = "uas_einsatz"
    __table_args__ = (
        Index("ix_uas_einsatz_org", "org_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped

    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=UASEinsatzStatus.alarmiert.value
    )

    alarmierung_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    anmeldung_el_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    abmeldung_el_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    tetra_rufname: Mapped[str | None] = mapped_column(String(60), nullable=True)
    betreibernummer: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Kommunikationsmatrix (JSON: {el_sprechgruppe, flug_sprechgruppe, tmo_dmo, luftfahrt_abstimmung})
    kommunikationsmatrix: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Risikobewertung (JSON: {gelände, menschen, luftraum, wetter, sonstiges, gesamt})
    risikobewertung: Mapped[str | None] = mapped_column(Text, nullable=True)

    einsatzgrund: Mapped[str | None] = mapped_column(Text, nullable=True)
    datenschutz_bestaetigt: Mapped[bool] = mapped_column(Boolean, default=False)
    gesamteinsatzleiter: Mapped[str | None] = mapped_column(String(150), nullable=True)
    notizen: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    rollen: Mapped[list[UASEinsatzRolleEintrag]] = relationship(
        back_populates="einsatz", cascade="all, delete-orphan"
    )


class UASEinsatzRolleEintrag(TenantScoped, Base):
    """Rollenbesetzung im Drohneneinsatz (RL 5.2–5.7, 6.1)."""
    __tablename__ = "uas_einsatz_rolle"
    __table_args__ = (
        Index("ix_uas_einsatz_rolle_einsatz", "uas_einsatz_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped

    uas_einsatz_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("uas_einsatz.id", ondelete="CASCADE"), nullable=False
    )
    pilot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("uas_pilot.id", ondelete="SET NULL"), nullable=True
    )
    helfer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    rolle: Mapped[str] = mapped_column(String(40), nullable=False)
    override_begruendung: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    einsatz: Mapped[UASEinsatz] = relationship(back_populates="rollen")
    pilot: Mapped[UASPilot | None] = relationship()


# ── Flugbuch & Checklisten (PR 4) ─────────────────────────────────────────────

class UASFlugDurchfuehrung(str, enum.Enum):
    vlos = "vlos"
    evlos = "evlos"
    bvlos = "bvlos"


class UASFlugGrundlage(str, enum.Enum):
    open_a1 = "open_a1"
    open_a2 = "open_a2"
    open_a3 = "open_a3"
    specific_bescheid = "specific_bescheid"


class UASFlugStatus(str, enum.Enum):
    offen = "offen"
    abgeschlossen = "abgeschlossen"


class UASChecklisteTyp(str, enum.Enum):
    vorflug = "vorflug"
    nachflug = "nachflug"
    check = "check"


class UASFlug(TenantScoped, Base):
    """Flugbuch-Eintrag je Flug (RL Anh. 8.1, 8.2 v9). Append-only nach Abschluss."""
    __tablename__ = "uas_flug"
    __table_args__ = (
        Index("ix_uas_flug_einsatz", "uas_einsatz_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped

    uas_einsatz_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("uas_einsatz.id", ondelete="RESTRICT"), nullable=False
    )
    lfd_nr: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    datum: Mapped[date] = mapped_column(Date, nullable=False)

    pilot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("uas_pilot.id", ondelete="SET NULL"), nullable=True
    )
    device_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("uas_device.id", ondelete="SET NULL"), nullable=True
    )

    start_ort: Mapped[str | None] = mapped_column(String(200), nullable=True)
    landung_ort: Mapped[str | None] = mapped_column(String(200), nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    landung_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dauer_min: Mapped[int | None] = mapped_column(Integer, nullable=True)  # berechnet

    durchfuehrung: Mapped[str] = mapped_column(
        String(10), nullable=False, default=UASFlugDurchfuehrung.vlos.value
    )
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    grundlage: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UASFlugGrundlage.open_a1.value
    )
    bescheid_nr: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Berechnete Felder (RL Anh. 8.2/v9)
    geplante_flughoehe_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    contingency_volume_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ground_risk_buffer_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    abstand_menschenansammlung_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    flughoehe_konform: Mapped[bool] = mapped_column(Boolean, default=False)  # 1:1-Regel

    nachtbetrieb: Mapped[bool] = mapped_column(Boolean, default=False)
    beleuchtung_bestaetigt: Mapped[bool] = mapped_column(Boolean, default=False)

    gesamteinsatzleiter: Mapped[str | None] = mapped_column(String(150), nullable=True)
    einsatzleiter_drohne: Mapped[str | None] = mapped_column(String(150), nullable=True)
    unfall: Mapped[bool] = mapped_column(Boolean, default=False)
    bemerkungen: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(15), nullable=False, default=UASFlugStatus.offen.value
    )
    inhalt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    checklisten: Mapped[list[UASCheckliste]] = relationship(
        back_populates="flug", cascade="all, delete-orphan"
    )


class UASCheckliste(TenantScoped, Base):
    """Vor-/Nachflug-Checkliste mit 4-Augen-Prinzip (RL 4.2, Anh. 8.2 v9)."""
    __tablename__ = "uas_checkliste"
    __table_args__ = (
        Index("ix_uas_checkliste_flug", "uas_flug_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped

    uas_flug_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("uas_flug.id", ondelete="CASCADE"), nullable=False
    )
    typ: Mapped[str] = mapped_column(
        String(15), nullable=False, default=UASChecklisteTyp.vorflug.value
    )
    punkte: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    erledigt_von_pilot: Mapped[str | None] = mapped_column(String(150), nullable=True)
    erledigt_von_zweitperson: Mapped[str | None] = mapped_column(String(150), nullable=True)
    abgeschlossen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    flug: Mapped[UASFlug] = relationship(back_populates="checklisten")


# ── Ereignis / Notfall / Unfall (PR 5) ────────────────────────────────────────

class UASEreignisTyp(str, enum.Enum):
    notfall = "notfall"
    unfall = "unfall"
    stoerung = "stoerung"


class UASEreignis(TenantScoped, Base):
    """Meldepflichtiges Ereignis: Notfall, Unfall (ACG), Störung (RL 4.7, Anh. 8.3/8.4)."""
    __tablename__ = "uas_ereignis"
    __table_args__ = (
        Index("ix_uas_ereignis_flug", "uas_flug_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped

    uas_flug_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("uas_flug.id", ondelete="SET NULL"), nullable=True
    )
    typ: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UASEreignisTyp.stoerung.value
    )
    kategorie: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Zeitangaben (UTC + lokal, RL Anh. 8.4)
    zeit_lokal: Mapped[str | None] = mapped_column(String(10), nullable=True)   # HH:MM
    datum_lokal: Mapped[date | None] = mapped_column(Date, nullable=True)
    zeit_utc: Mapped[str | None] = mapped_column(String(10), nullable=True)
    datum_utc: Mapped[date | None] = mapped_column(Date, nullable=True)

    ort_icao: Mapped[str | None] = mapped_column(String(10), nullable=True)
    koordinaten: Mapped[str | None] = mapped_column(Text, nullable=True)  # GeoJSON
    klassifizierung: Mapped[str | None] = mapped_column(String(100), nullable=True)
    beschreibung: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Maßnahmen + Meldekette (JSON)
    massnahmen: Mapped[str | None] = mapped_column(Text, nullable=True)
    gemeldet_an: Mapped[str | None] = mapped_column(Text, nullable=True)  # {stuetzpunktleiter, behoerde_acg, ...}

    acg_export_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inhalt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
