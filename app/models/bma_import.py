"""BMA-Webplattform-Import (Landeswarnzentrale Vorarlberg): Org-Konfiguration +
Dedup-/Diff-Merker je importierter Brandmeldeanlage.

Siehe app/services/bma_import/ fuer Client (Kendo-Grid-JSON + Detailseiten-HTML),
Parser und Sync-Orchestrierung. Muster: app/models/dibos.py (Org-Config),
app/models/lis.py::LisSyncedObject (Dedup-Merker mit UniqueConstraint).

Anders als LisSyncedObject ist BmaImportSatz kein reiner Dedup-Merker, sondern
haelt zusaetzlich den zuletzt gesehenen Rohstand (rohdaten_json/quell_hash) vor -
er dient als Grundlage fuer die Diff-Anzeige der Review-Queue (siehe
app/services/bma_import/bma_sync.py::baue_diff). Es gibt bewusst KEINE eigene
Vorschlagstabelle (anders als ObjektStammdatenVorschlag): der Importsatz selbst
ist der Vorschlag, der Diff wird zur Anzeigezeit berechnet.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base

# letzter_lauf_status / BmaImportLauf.status
BMA_LAUF_OK = "ok"
BMA_LAUF_FEHLER = "fehler"
BMA_LAUF_SESSION_ABGELAUFEN = "session_abgelaufen"

# BmaImportSatz.zuordnung
BMA_ZUORDNUNG_AUTO = "auto"
BMA_ZUORDNUNG_MANUELL = "manuell"
BMA_ZUORDNUNG_OFFEN = "offen"

# BmaImportSatz.status
BMA_SATZ_AKTIV = "aktiv"
BMA_SATZ_VERSCHWUNDEN = "verschwunden"


class OrgBmaImportConfig(Base):
    """BMA-Webplattform-Verbindung einer Organisation (1:1 je Org). Muster: OrgDibosConfig.

    Der Portal-Login hat eine Anti-Roboter-Verifizierung (Friendly Captcha) und
    teils 2FA - ein automatischer Login ist damit ausgeschlossen. Stattdessen wird
    ein manuell im Browser erzeugtes Session-Cookie hinterlegt (Fernet-
    verschluesselt), mit dem die BMA-Webplattform selbst (getrennt vom
    DIBOS-Portal-Login) cookie-basiert authentifiziert.
    """
    __tablename__ = "org_bma_import_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    base_url: Mapped[str | None] = mapped_column(
        String(300), nullable=True, default="https://dibos.lwz-vorarlberg.at/LWZ_BMA_Webplattform"
    )
    # Kompletter Cookie-String (nicht nur ein einzelner Name) - robust gegen
    # Umbenennungen der Session-Cookies auf Betreiberseite.
    session_cookie_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-verschluesselt
    session_gesetzt_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Haelt die hinterlegte Session zwischen den taeglichen Laeufen aktiv (siehe
    # bma_loop.py) - ohne Keepalive ist unklar, wie lange ein Cookie gueltig bleibt.
    keepalive_aktiv: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    sync_stunde: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    sync_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # Unbekannte Anlage -> neues Entwurf-Objekt anlegen statt nur zu protokollieren
    auto_anlegen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Rechnungsadresse (Betreiber-/Verwaltungsfirma) mitimportieren - Default aus,
    # da hierbei eine zusaetzliche Kopie personenbezogener Daten entsteht, die im
    # Verarbeitungsverzeichnis der Org zu ergaenzen waere.
    import_rechnungsadresse: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    letzter_lauf_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    letzter_lauf_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    letzter_lauf_meldung: Mapped[str | None] = mapped_column(String(300), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")

    @property
    def is_fully_configured(self) -> bool:
        return bool(self.base_url and self.session_cookie_enc)


class BmaImportSatz(TenantScoped, Base):
    """Externe Identitaet + letzter Rohstand einer importierten BMA-Anlage.

    UniqueConstraint(org_id, extern_id): eine Zeile je Anlage der Quelle (Muster
    LisSyncedObject). `objekt_id` bleibt einmal gesetzt stabil - eine spaetere
    Umbenennung/Verschiebung des Objekts loest keine erneute Zuordnungssuche aus
    (siehe bma_sync.py::finde_ziel_objekt).
    """
    __tablename__ = "bma_import_satz"
    __table_args__ = (
        UniqueConstraint("org_id", "extern_id", name="uq_bma_import_satz_org_extern"),
        Index("ix_bma_import_satz_org_status", "org_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    extern_id: Mapped[str] = mapped_column(String(50), nullable=False)  # AlarmSystem.Id
    extern_guid: Mapped[str | None] = mapped_column(String(64), nullable=True)  # AlarmSystem.Guid
    objekt_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("objekt.id", ondelete="SET NULL"), nullable=True
    )
    bma_nummer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bezeichnung: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Letzter geparster Stand (Diff-Quelle fuer die Review-Queue, siehe Moduldoc)
    rohdaten_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    quell_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Stand, den ein Verwalter uebernommen oder ausdruecklich ignoriert hat - der
    # Satz erscheint erst wieder in der Queue, wenn sich quell_hash davon unterscheidet.
    bestaetigt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quell_change_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detail_geholt_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Zuordnung: auto (automatisch gefunden/angelegt) / manuell / offen (kein Objekt)
    zuordnung: Mapped[str] = mapped_column(String(20), nullable=False, default=BMA_ZUORDNUNG_OFFEN)
    # Status: aktiv / verschwunden (nicht mehr in der Quelle, Objekt bleibt erhalten)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=BMA_SATZ_AKTIV)

    erst_gesehen_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    zuletzt_gesehen_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    zuletzt_geaendert_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BmaImportLauf(TenantScoped, Base):
    """Laufprotokoll eines BMA-Import-Durchlaufs (Anzeige im Admin-UI)."""
    __tablename__ = "bma_import_lauf"
    __table_args__ = (Index("ix_bma_import_lauf_org_gestartet", "org_id", "gestartet_am"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    gestartet_am: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    beendet_am: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Ausloeser: loop (taeglicher Lauf) / manuell ("Jetzt importieren" im Admin-UI)
    ausloeser: Mapped[str] = mapped_column(String(20), nullable=False, default="loop")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=BMA_LAUF_OK)

    gefunden: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    neu_angelegt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    aktualisiert: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vorschlaege: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uebersprungen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fehler: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    meldung: Mapped[str | None] = mapped_column(String(500), nullable=True)
