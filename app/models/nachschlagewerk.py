"""Nachschlagewerke: Rettungsdatenblatt-Cache (Fahrzeug-Rettungskarten).

Geteiltes Nachschlagewerk ohne Org-Bezug (KEIN TenantScoped) — dieselbe
Rettungskarte gilt fuer alle Organisationen. On-demand geladene PDFs werden hier
zwischengespeichert (Metadaten + Dateipfad), damit sie nach dem ersten Abruf
offline (Service-Worker cache-first, /nachschlagewerk-cache/) verfuegbar sind.

Die Datei selbst liegt im Dateisystem unter
NACHSCHLAGEWERK_DATA_DIR/rettungskarten/{uuid}/original.pdf; hier steht nur der
relative Pfad (Muster: objekt_dokument.pfad).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RettungsdatenblattCache(Base):
    """Zwischengespeichertes Rettungsdatenblatt zu einem Fahrzeugmodell (global)."""
    __tablename__ = "rettungsdatenblatt_cache"
    __table_args__ = (
        UniqueConstraint("hersteller", "modell", "baujahr_von",
                         name="uq_rdb_hersteller_modell_baujahr"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hersteller: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    modell: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    baujahr_von: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baujahr_bis: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kraftstoff: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Herkunft (Quell-URL/Anbieter) — Nachweis + spaeteres Re-Fetch
    quelle: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Relativer Dateipfad unter NACHSCHLAGEWERK_DATA_DIR (None = nur Deep-Link bekannt)
    pfad: Mapped[str | None] = mapped_column(String(300), nullable=True)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    abgerufen_am: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC))

    @property
    def hat_pdf(self) -> bool:
        return bool(self.pfad)

    @property
    def anzeige_name(self) -> str:
        teile = [self.hersteller, self.modell]
        if self.baujahr_von:
            spanne = str(self.baujahr_von)
            if self.baujahr_bis and self.baujahr_bis != self.baujahr_von:
                spanne += f"-{self.baujahr_bis}"
            teile.append(f"({spanne})")
        return " ".join(t for t in teile if t)


class RettungskartenKatalog(Base):
    """Suchbarer Katalog verfuegbarer Rettungskarten (Euro NCAP / CTIF "Euro Rescue").

    Reines Verzeichnis (Metadaten + direkter PDF-Link je Modell) — KEINE Spiegelung:
    das eigentliche PDF wird erst beim Oeffnen on-demand in RettungsdatenblattCache
    geladen und dann offline (SW cache-first) vorgehalten. Taeglich per Sync befuellt;
    `quelle_id` ist die stabile Euro-Rescue-Variant-ID (Upsert-Schluessel).
    """
    __tablename__ = "rettungskarten_katalog"
    __table_args__ = (
        UniqueConstraint("quelle_id", name="uq_rkk_quelle_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quelle_id: Mapped[str] = mapped_column(String(40), nullable=False)
    hersteller: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    modell: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    karosserie: Mapped[str | None] = mapped_column(String(60), nullable=True)
    baujahr_von: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baujahr_bis: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tueren: Mapped[int | None] = mapped_column(Integer, nullable=True)
    antrieb: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # Direkter Rettungsblatt-PDF-Link (bevorzugt deutsch) + Sprachkuerzel
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_sprache: Mapped[str | None] = mapped_column(String(8), nullable=True)
    bild_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    aktualisiert_am: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC))

    @property
    def anzeige_name(self) -> str:
        teile = [self.hersteller, self.modell]
        if self.karosserie:
            teile.append(self.karosserie)
        if self.baujahr_von:
            spanne = str(self.baujahr_von)
            if self.baujahr_bis and self.baujahr_bis != self.baujahr_von:
                spanne += f"-{self.baujahr_bis}"
            teile.append(f"({spanne})")
        return " ".join(t for t in teile if t)
