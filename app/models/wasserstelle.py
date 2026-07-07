"""Wasserstellen-/Löschwasser-Stammdaten je Org (Hydranten, Saugstellen, Löschteiche …).

Anders als der OSM-Hydranten-Layer (live via Overpass) sind das manuell gepflegte,
verbindliche Entnahmestellen der Feuerwehr (Import aus EUS/GIS-CSV oder Handpflege).
Auf der Einsatzinfo-Karte haben sie Vorrang; OSM zeigt nur noch Entnahmestellen von
Nachbarorten, für die keine eigenen Stammdaten existieren.

Die Tabelle ist TenantScoped (org-isoliert) und in _TENANT_TABLE_NAMES
(app/core/tenant.py) registriert.
"""
from __future__ import annotations

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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base

# Fachliche Typen einer Wasserstelle (Code → Label). Der Code ist stabil (Import,
# Filter). Das Karten-Icon leitet sich über WASSERSTELLE_ICON_KAT ab.
WASSERSTELLE_TYPEN: dict[str, str] = {
    "ueberflur": "Überflurhydrant",
    "unterflur": "Unterflurhydrant",
    "saugstelle": "Saugstelle",
    "loeschteich": "Löschteich",
    "loeschbehaelter": "Löschwasserbehälter",
    "brunnen": "Brunnen / offenes Gewässer",
    "relais": "Relais-/Pumpenstandort",
    "sonstige": "Sonstige Entnahmestelle",
}

# Betriebszustand einer Wasserstelle (feiner als das binäre aktiv). 'aktiv' wird beim
# Speichern daraus abgeleitet: nur 'defekt' => aktiv=False, damit die operativen Filter
# (Einsatzinfo / Nachbar-Gefahren) defekte Stellen ausblenden, Wartung aber verfügbar bleibt.
WASSERSTELLE_STATUS: dict[str, str] = {
    "bereit": "Bereit",
    "wartung": "Wartung",
    "defekt": "Defekt",
}

# Abbildung fachlicher Typ → Karten-Icon-Kategorie (deckt sich mit den bestehenden
# Hydranten-Icons in einsatz_info.js / app.css: ueberflur / unterflur / loeschwasser).
WASSERSTELLE_ICON_KAT: dict[str, str] = {
    "ueberflur": "ueberflur",
    "unterflur": "unterflur",
    "saugstelle": "loeschwasser",
    "loeschteich": "loeschwasser",
    "loeschbehaelter": "loeschwasser",
    "brunnen": "loeschwasser",
    "relais": "loeschwasser",
    "sonstige": "loeschwasser",
}


class Wasserstelle(TenantScoped, Base):
    """Manuell gepflegte Löschwasser-Entnahmestelle (Stammdaten)."""

    __tablename__ = "wasserstelle"
    __table_args__ = (
        Index("ix_wasserstelle_org_typ", "org_id", "typ"),
        Index("ix_wasserstelle_org_geo", "org_id", "lat", "lng"),
        # Idempotenz-Anker für den CSV-Import (Bezeichnung + gerundete Koordinate)
        Index("ix_wasserstelle_org_import", "org_id", "import_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    bezeichnung: Mapped[str] = mapped_column(String(250), nullable=False)
    typ: Mapped[str] = mapped_column(String(20), nullable=False, default="ueberflur")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    hinweis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ergiebigkeit (Liter/Minute), optional — für spätere Handpflege
    ergiebigkeit_l_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Herkunft: 'import' (CSV/GIS) oder 'manuell'
    quelle: Mapped[str] = mapped_column(String(20), nullable=False, default="manuell")
    # Stabiler Schlüssel für idempotenten Import (leer bei Handanlage)
    import_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Betriebszustand: bereit / wartung / defekt (siehe WASSERSTELLE_STATUS)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="bereit", server_default="bereit")

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
    def typ_label(self) -> str:
        return WASSERSTELLE_TYPEN.get(self.typ, self.typ)

    @property
    def status_label(self) -> str:
        return WASSERSTELLE_STATUS.get(self.status, self.status)

    @property
    def icon_kat(self) -> str:
        return WASSERSTELLE_ICON_KAT.get(self.typ, "loeschwasser")
