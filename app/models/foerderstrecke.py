"""Förderstrecken-Planer — Gerätekatalog (Pumpen- und Schlauchtypen) je Org.

Grundlage für die Hydraulik-Engine (foerderstrecke_service.py): jede Organisation
verwaltet ihre Pumpen und Schläuche selbst. Mitgelieferte **Vorlagen** (siehe
app/data/foerder_vorlagen.py) dienen nur als Startpunkt; „Aus Vorlage anlegen" erzeugt
eine frei editierbare, org-eigene Kopie (quelle="vorlage"). Der Org-Katalog bleibt leer,
bis der Anwender bewusst eine Vorlage übernimmt oder eine eigene Pumpe anlegt.

Kennlinien werden als editierbare Q-H-Punktlisten je Drehzahlstufe im Feld
`kennlinien_json` hinterlegt ({rpm: [[Q_l_min, H_m], …]}), nicht als Formel — so lassen
sich Herstellerkurven digitalisieren und später per Übungsmessung kalibrieren (PR 7).

Beide Tabellen sind TenantScoped (org-isoliert) und in _TENANT_TABLE_NAMES
(app/core/tenant.py) registriert.
"""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
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

# Herkunft eines Katalogeintrags (analog wasserstelle.quelle).
QUELLE_MANUELL = "manuell"
QUELLE_VORLAGE = "vorlage"

# Status einer geplanten Förderstrecke
STRECKE_STATUS_ENTWURF = "entwurf"
STRECKE_STATUS_FREIGEGEBEN = "freigegeben"
STRECKE_STATUS_ARCHIVIERT = "archiviert"
STRECKE_STATUS = {
    STRECKE_STATUS_ENTWURF: "Entwurf",
    STRECKE_STATUS_FREIGEGEBEN: "Freigegeben",
    STRECKE_STATUS_ARCHIVIERT: "Archiviert",
}

# Stationstypen (decken sich mit der Engine, foerderstrecke_service.PumpenStation.typ)
STATION_QUELLPUMPE = "quellpumpe"
STATION_VERSTAERKER = "verstaerker"
STATION_PUFFER = "puffer"
STATION_UEBERGABE = "uebergabe"
STATION_TYPEN = {
    STATION_QUELLPUMPE: "Quellpumpe (Ansaugung)",
    STATION_VERSTAERKER: "Verstärkerpumpe (geschlossene Reihe)",
    STATION_PUFFER: "Pufferstation (offene Reihe)",
    STATION_UEBERGABE: "Übergabe-/Verteilstation",
}

# Status eines Kalibrier-Vorschlags (Review-Queue, nie Auto-Apply)
KALIBRIER_OFFEN = "offen"
KALIBRIER_UEBERNOMMEN = "uebernommen"
KALIBRIER_VERWORFEN = "verworfen"


def wasserinhalt_pro_meter(durchmesser_mm: int | float | None) -> float | None:
    """Wasserinhalt einer Leitung in Liter je laufendem Meter aus dem Innendurchmesser.

    Kreisquerschnitt A = π·(d/2)²; 1 m³ = 1000 l. F-150 → 17,7 l/m, B-75 → 4,4 l/m.
    """
    if not durchmesser_mm or durchmesser_mm <= 0:
        return None
    r_m = (float(durchmesser_mm) / 1000.0) / 2.0
    return round(math.pi * r_m * r_m * 1000.0, 2)


def _lade_json(roh: str | None, fallback):
    if not roh:
        return fallback
    try:
        return json.loads(roh)
    except (ValueError, TypeError):
        return fallback


class FoerderPumpenTyp(TenantScoped, Base):
    """Pumpentyp im org-eigenen Katalog (HLP, TS, Fremdpumpe) mit Kennlinie(n)."""

    __tablename__ = "foerder_pumpen_typ"
    __table_args__ = (
        Index("ix_foerder_pumpen_typ_org_aktiv", "org_id", "aktiv"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    # Kennlinie(n) je Drehzahlstufe: {"2000": [[Q_l_min, H_m], …], …}
    kennlinien_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Druckabgänge (Nennweite mm) und maximale Parallelzahl je Strecke
    druck_anschluss_dn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    druck_parallel_max: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Sauganschlüsse (Nennweite mm) und maximale Parallelzahl
    saug_anschluss_dn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saug_parallel_max: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    max_ansaughoehe_m: Mapped[float] = mapped_column(Float, nullable=False, default=7.5)
    min_eingangsdruck_bar: Mapped[float] = mapped_column(Float, nullable=False, default=1.5)
    max_ausgangsdruck_bar: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Optionale NPSHr-Derating-Punktliste [[Q_l_min, NPSHr_m], …]
    npshr_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    tank_l: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Verbrauch je Drehzahlstufe: {"2000": 19.5, …} (Liter/Stunde)
    verbrauch_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    vehicle_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("vehicle_master.id", ondelete="SET NULL"), nullable=True
    )
    hinweise: Mapped[str | None] = mapped_column(Text, nullable=True)
    foto_pfad: Mapped[str | None] = mapped_column(String(500), nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Herkunft: 'manuell' (frei angelegt) oder 'vorlage' (aus Vorlage kopiert)
    quelle: Mapped[str] = mapped_column(String(20), nullable=False, default=QUELLE_MANUELL)
    # Schlüssel der Vorlage, aus der kopiert wurde (nur Info „basiert auf …")
    vorlage_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

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

    @property
    def kennlinien(self) -> dict[str, list[list[float]]]:
        """Kennlinien-Dict {rpm: [[Q,H], …]} (leer bei fehlenden/kaputten Daten)."""
        return _lade_json(self.kennlinien_json, {})

    @property
    def npshr(self) -> list[list[float]]:
        return _lade_json(self.npshr_json, [])

    @property
    def verbrauch(self) -> dict[str, float]:
        return _lade_json(self.verbrauch_json, {})

    @property
    def drehzahlstufen(self) -> list[str]:
        """Sortierte Liste der hinterlegten Drehzahlstufen (als String-Keys).

        Numerische Stufen (rpm) werden nach Zahl sortiert; nicht-numerische
        Labels (z. B. 'nenn' bei fest laufenden Tragkraftspritzen) danach.
        """
        def _key(k: str) -> tuple[int, float, str]:
            try:
                return (0, float(k), "")
            except (ValueError, TypeError):
                return (1, 0.0, k)
        return sorted(self.kennlinien.keys(), key=_key)


class FoerderSchlauchTyp(TenantScoped, Base):
    """Schlauchtyp im org-eigenen Katalog (F-150, A-110, B-75 …) mit Verlustbeiwert."""

    __tablename__ = "foerder_schlauch_typ"
    __table_args__ = (
        Index("ix_foerder_schlauch_typ_org_aktiv", "org_id", "aktiv"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    kuerzel: Mapped[str] = mapped_column(String(30), nullable=False)
    durchmesser_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    # Verlustbeiwert in bar je 100 m bei 1000 l/min (intern quadratisch skaliert)
    k_verlust: Mapped[float] = mapped_column(Float, nullable=False)
    element_laenge_m: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    max_betriebsdruck_bar: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Wasserinhalt je Meter (aus Durchmesser berechnet, beim Speichern gesetzt)
    wasserinhalt_l_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    vorrat_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    quelle: Mapped[str] = mapped_column(String(20), nullable=False, default=QUELLE_MANUELL)
    vorlage_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

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


class Foerderstrecke(TenantScoped, Base):
    """Geplante/berechnete Förderstrecke (Ansaug → Pumpen/Relais → Auslass)."""

    __tablename__ = "foerderstrecke"
    __table_args__ = (
        Index("ix_foerderstrecke_org_status", "org_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STRECKE_STATUS_ENTWURF, server_default=STRECKE_STATUS_ENTWURF)

    objekt_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="SET NULL"), nullable=True)
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="SET NULL"), nullable=True)
    # major_incident.id ist Integer (INT) — FK-Spalte muss denselben Typ haben,
    # sonst MySQL errno 150 (Foreign key constraint is incorrectly formed).
    lage_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("major_incident.id", ondelete="SET NULL"), nullable=True)

    # GeoJSON der Route (LineString) sowie Anker- und Parameterdaten als JSON-Text
    route_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)
    ansaug_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    auslass_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    hoehenprofil_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameter_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    aktualisiert_am: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    erstellt_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    aktualisiert_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)

    stationen: Mapped[list["FoerderStation"]] = relationship(
        back_populates="strecke", cascade="all, delete-orphan",
        order_by="FoerderStation.strang_nr, FoerderStation.sort")
    ergebnisse: Mapped[list["FoerderErgebnis"]] = relationship(
        back_populates="strecke", cascade="all, delete-orphan",
        order_by="FoerderErgebnis.berechnet_am")

    @property
    def status_label(self) -> str:
        return STRECKE_STATUS.get(self.status, self.status)

    @property
    def parameter(self) -> dict:
        return _lade_json(self.parameter_json, {})

    @property
    def ansaug(self) -> dict:
        return _lade_json(self.ansaug_json, {})

    @property
    def auslass(self) -> dict:
        return _lade_json(self.auslass_json, {})


class FoerderStation(TenantScoped, Base):
    """Pumpen-/Relaisstation einer Förderstrecke (in Strangreihenfolge)."""

    __tablename__ = "foerder_station"
    __table_args__ = (
        Index("ix_foerder_station_strecke", "strecke_id", "strang_nr", "sort"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    strecke_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("foerderstrecke.id", ondelete="CASCADE"), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strang_nr: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    typ: Mapped[str] = mapped_column(String(20), nullable=False, default=STATION_VERSTAERKER)

    pumpen_typ_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("foerder_pumpen_typ.id", ondelete="SET NULL"), nullable=True)
    rpm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    druck_parallel: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schlauch_typ_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("foerder_schlauch_typ.id", ondelete="SET NULL"), nullable=True)
    # Geometrie des Abschnitts hinter dieser Station (für Neuberechnung/PDF)
    abschnitt_laenge_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    abschnitt_delta_hoehe_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    saug_parallel: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    behaelter_volumen_l: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # nur typ=uebergabe: Abgangsstränge (Anzahl + Schlauchtyp je Strang) als JSON
    abgang_straenge: Mapped[str | None] = mapped_column(Text, nullable=True)
    wasserstelle_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("wasserstelle.id", ondelete="SET NULL"), nullable=True)

    strecke: Mapped["Foerderstrecke"] = relationship(back_populates="stationen")

    @property
    def typ_label(self) -> str:
        return STATION_TYPEN.get(self.typ, self.typ)


class FoerderErgebnis(TenantScoped, Base):
    """Versioniertes Berechnungsergebnis einer Förderstrecke (geplant vs. neu)."""

    __tablename__ = "foerder_ergebnis"
    __table_args__ = (
        Index("ix_foerder_ergebnis_strecke", "strecke_id", "berechnet_am"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    strecke_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("foerderstrecke.id", ondelete="CASCADE"), nullable=False)
    berechnet_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    q_max_l_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    modus: Mapped[str] = mapped_column(String(1), nullable=False, default="A")
    stationswerte_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnungen_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    strecke: Mapped["Foerderstrecke"] = relationship(back_populates="ergebnisse")

    @property
    def stationswerte(self) -> list:
        return _lade_json(self.stationswerte_json, [])

    @property
    def material(self) -> dict:
        return _lade_json(self.material_json, {})

    @property
    def warnungen(self) -> list:
        return _lade_json(self.warnungen_json, [])


class FoerderMessung(TenantScoped, Base):
    """Gemessene Werte aus Übung/Nassbewerb — Grundlage der Kalibrierung (PR 7)."""

    __tablename__ = "foerder_messung"
    __table_args__ = (
        Index("ix_foerder_messung_strecke", "strecke_id", "datum"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    strecke_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("foerderstrecke.id", ondelete="SET NULL"), nullable=True)
    station_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("foerder_station.id", ondelete="SET NULL"), nullable=True)
    schlauch_typ_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("foerder_schlauch_typ.id", ondelete="SET NULL"), nullable=True)
    datum: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    q_gemessen_l_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    laenge_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_parallel: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    delta_hoehe_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    p_aus_bar: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_ein_folge_bar: Mapped[float | None] = mapped_column(Float, nullable=True)
    notiz: Mapped[str | None] = mapped_column(Text, nullable=True)
    erstellt_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)


class FoerderKalibrierVorschlag(TenantScoped, Base):
    """Kalibrier-Vorschlag je Schlauchtyp (korrigierter k-Wert) — Review-Queue.

    Wird NIE automatisch übernommen: der Sachbearbeiter bestätigt/verwirft (Muster
    ObjektSeiteKiVorschlag).
    """

    __tablename__ = "foerder_kalibrier_vorschlag"
    __table_args__ = (
        Index("ix_foerder_kalibrier_org_status", "org_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    schlauch_typ_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("foerder_schlauch_typ.id", ondelete="CASCADE"), nullable=False)
    k_alt: Mapped[float | None] = mapped_column(Float, nullable=True)
    k_neu: Mapped[float] = mapped_column(Float, nullable=False)
    n_messungen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    begruendung: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=KALIBRIER_OFFEN, server_default=KALIBRIER_OFFEN)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    entschieden_von_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    entschieden_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FoerderMaschinistToken(TenantScoped, Base):
    """Login-freier Token für die Maschinisten-Zettel-Seite einer Strecke (Muster LagekarteToken).

    Nur der SHA-256-Hash wird gespeichert; der Klartext-Token wird einmalig beim Anlegen
    angezeigt. Die öffentliche Route scopet ausschließlich über token.org_id (SEC-11).
    """

    __tablename__ = "foerder_maschinist_token"
    __table_args__ = (
        Index("ix_foerder_maschinist_token_hash", "token_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    strecke_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("foerderstrecke.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    widerrufen_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    zuletzt_genutzt_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_active(self) -> bool:
        return self.widerrufen_am is None
