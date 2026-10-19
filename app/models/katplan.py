"""Tenant-gebundene Arbeitsstaende der Katastrophenplaene."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base

KATPLAN_STATUS = ("entwurf", "in_pruefung", "freigegeben", "in_ueberarbeitung", "archiviert")
KATPLAN_STATUS_LABELS = {
    "entwurf": "Entwurf", "in_pruefung": "In Prüfung", "freigegeben": "Freigegeben",
    "in_ueberarbeitung": "In Überarbeitung", "archiviert": "Archiviert",
}
KATPLAN_GEFAHRENARTEN = {
    "hochwasser": "Hochwasser", "sturm": "Sturm", "blackout": "Blackout/Stromausfall",
    "evakuierung": "Evakuierung", "schnee": "Schneedruck/Lawine", "hitze": "Hitze/Trockenheit",
    "erdbeben": "Erdbeben", "gefahrstoff": "Gefahrstoff", "pandemie": "Pandemie", "sonstige": "Sonstige",
}
KATPLAN_HERKUNFT = ("manual", "imported", "ai_suggested")
KATPLAN_GEO_STATUS = ("keine", "ungeprueft", "geprueft")
KATPLAN_PRIORITAETEN = {1: "Hoch", 2: "Normal", 3: "Niedrig"}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class KatplanPlan(TenantScoped, Base):
    __tablename__ = "katplan_plan"
    __table_args__ = (
        sa.Index("ix_katplan_plan_org_status", "org_id", "status"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(sa.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    titel: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    beschreibung_html: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    gefahrenarten_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    geltungsbereich: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)
    verantwortliche_stelle: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    verantwortlich_kontakt_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("kontakt.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="entwurf")
    aktuelle_version_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    wiedervorlage_am: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    wiedervorlage_erinnert_am: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    erstellt_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    aktualisiert_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    erstellt_von_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    aktualisiert_von_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def gefahrenarten(self) -> list[str]:
        try:
            daten = json.loads(self.gefahrenarten_json or "[]")
        except (ValueError, TypeError):
            return []
        return [wert for wert in daten if isinstance(wert, str)] if isinstance(daten, list) else []


class KatplanAbschnitt(TenantScoped, Base):
    __tablename__ = "katplan_abschnitt"
    __table_args__ = (
        sa.Index("ix_katplan_abschnitt_org_plan", "org_id", "plan_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_abschnitt.id", ondelete="CASCADE"), nullable=True
    )
    titel: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    beschreibung_html: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    sort: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)


class KatplanPhase(TenantScoped, Base):
    __tablename__ = "katplan_phase"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    beschreibung: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    ausloeser: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    farbe: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    sort: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)


class KatplanElement(TenantScoped, Base):
    __tablename__ = "katplan_element"
    __table_args__ = (
        sa.Index("ix_katplan_element_org_plan_sort", "org_id", "plan_id", "sort"),
        sa.Index("ix_katplan_element_org_typ", "org_id", "typ"),
        sa.UniqueConstraint("plan_id", "uuid"),
        sa.UniqueConstraint("plan_id", "kennung"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(sa.String(36), nullable=False, default=lambda: str(uuid4()))
    plan_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False
    )
    abschnitt_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_abschnitt.id", ondelete="SET NULL"), nullable=True
    )
    kennung: Mapped[str | None] = mapped_column(sa.String(30), nullable=True)
    typ: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    titel: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    beschreibung_html: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    daten_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    prioritaet: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=2)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="entwurf")
    tags_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    gueltig_bis: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    pruefen_am: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    vertraulich: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    strasse: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    hausnummer: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    plz: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)
    ort: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    lat: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    geometry_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    geo_status: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="keine")
    herkunft: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="manual")
    quelle_dokument_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    quelle_seiten: Mapped[str | None] = mapped_column(sa.String(30), nullable=True)
    gsl_vorlage: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    gsl_auftragsart: Mapped[str | None] = mapped_column(sa.String(12), nullable=True)
    gsl_prioritaet: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    gsl_ressourcentypen_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    gsl_dauer_min: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    gsl_voraussetzungen: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    suchtext: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    sort: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    erstellt_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    aktualisiert_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    erstellt_von_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    aktualisiert_von_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )


class KatplanElementPhase(TenantScoped, Base):
    __tablename__ = "katplan_element_phase"
    __table_args__ = (
        sa.UniqueConstraint("element_id", "phase_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    element_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_element.id", ondelete="CASCADE"), nullable=False
    )
    phase_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_phase.id", ondelete="CASCADE"), nullable=False
    )


class KatplanLink(TenantScoped, Base):
    __tablename__ = "katplan_link"
    __table_args__ = (
        sa.UniqueConstraint("element_id", "ziel_typ", "ziel_id", "rolle"),
        sa.Index("ix_katplan_link_org_ziel", "org_id", "ziel_typ", "ziel_id"),
        sa.Index("ix_katplan_link_org_plan", "org_id", "plan_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False
    )
    element_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_element.id", ondelete="CASCADE"), nullable=True
    )
    ziel_typ: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    ziel_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    rolle: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    hinweis: Mapped[str] = mapped_column(sa.String(500), nullable=False, default="")
    sort: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    erstellt_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    erstellt_von_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )


class KatplanRelation(TenantScoped, Base):
    __tablename__ = "katplan_relation"
    __table_args__ = (sa.UniqueConstraint("von_element_id", "zu_element_uuid", "art"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False
    )
    von_element_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_element.id", ondelete="CASCADE"), nullable=False
    )
    zu_element_uuid: Mapped[str] = mapped_column(sa.String(36), nullable=False)
    art: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    hinweis: Mapped[str] = mapped_column(sa.String(300), nullable=False, default="")


class KatplanAenderung(TenantScoped, Base):
    __tablename__ = "katplan_aenderung"
    __table_args__ = (
        sa.Index("ix_katplan_aenderung_org_plan_zeit", "org_id", "plan_id", "zeit"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False
    )
    element_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    entitaet: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    aktion: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    feld: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    alt: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    neu: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    quelle: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="ui")
    user_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    zeit: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)


class KatplanPruefung(TenantScoped, Base):
    __tablename__ = "katplan_pruefung"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    aktion: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    kommentar: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    zeit: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)


class KatplanVersion(TenantScoped, Base):
    __tablename__ = "katplan_version"
    __table_args__ = (
        sa.UniqueConstraint("plan_id", "versionsnummer"),
        sa.Index("ix_katplan_version_org_plan", "org_id", "plan_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False
    )
    versionsnummer: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    freigegeben_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    freigegeben_von_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    aenderungsnotiz: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    plan_json: Mapped[str] = mapped_column(sa.Text, nullable=False)
    inhalt_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    anzahl_elemente: Mapped[int] = mapped_column(sa.Integer, nullable=False)


class KatplanVersionElement(TenantScoped, Base):
    __tablename__ = "katplan_version_element"
    __table_args__ = (
        sa.UniqueConstraint("version_id", "element_uuid"),
        sa.Index("ix_katplan_version_element_org_version", "org_id", "version_id"),
        sa.Index("ix_katplan_version_element_org_uuid", "org_id", "element_uuid"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_version.id", ondelete="CASCADE"), nullable=False
    )
    element_uuid: Mapped[str] = mapped_column(sa.String(36), nullable=False)
    abschnitt_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    kennung: Mapped[str | None] = mapped_column(sa.String(30), nullable=True)
    typ: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    titel: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    beschreibung_html: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    daten_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    prioritaet: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=2)
    tags_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    gueltig_bis: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    pruefen_am: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    vertraulich: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    strasse: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    hausnummer: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    plz: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)
    ort: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    lat: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    geometry_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    geo_status: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="keine")
    herkunft: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="manual")
    quelle_dokument_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    quelle_seiten: Mapped[str | None] = mapped_column(sa.String(30), nullable=True)
    suchtext: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    sort: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    phasen_json: Mapped[str] = mapped_column(sa.Text, nullable=False, default="[]")
    relationen_json: Mapped[str] = mapped_column(sa.Text, nullable=False, default="[]", server_default="[]")
    links_json: Mapped[str] = mapped_column(sa.Text, nullable=False, default="[]")
    anhaenge_json: Mapped[str] = mapped_column(sa.Text, nullable=False, default="[]")
    gsl_json: Mapped[str] = mapped_column(sa.Text, nullable=False, default="{}")


@sa.event.listens_for(KatplanVersion, "before_update")
@sa.event.listens_for(KatplanVersionElement, "before_update")
def _version_unveraenderlich(mapper, connection, target) -> None:
    raise RuntimeError("Freigegebene Versionen sind unveränderlich")


class KatplanAnhang(TenantScoped, Base):
    __tablename__ = "katplan_anhang"
    __table_args__ = (
        sa.Index("ix_katplan_anhang_org_plan", "org_id", "plan_id"),
        sa.Index("ix_katplan_anhang_org_element", "org_id", "element_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(sa.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    plan_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"), nullable=False
    )
    abschnitt_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_abschnitt.id", ondelete="SET NULL"), nullable=True
    )
    element_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_element.id", ondelete="SET NULL"), nullable=True
    )
    art: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    dateiname: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    pfad: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    thumb_pfad: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    mime: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    groesse_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    breite: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    hoehe: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    objekt_dokument_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    seiten: Mapped[str | None] = mapped_column(sa.String(30), nullable=True)
    beschriftung: Mapped[str] = mapped_column(sa.String(300), nullable=False, default="")
    urheber: Mapped[str] = mapped_column(sa.String(150), nullable=False, default="")
    aufnahme_datum: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    vertraulich: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    sort: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    geloescht_am: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    erstellt_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    erstellt_von_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )


class KatplanDokument(TenantScoped, Base):
    __tablename__ = "katplan_dokument"
    __table_args__ = (sa.Index("ix_katplan_dokument_org_plan", "org_id", "plan_id"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(sa.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    plan_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"))
    titel: Mapped[str] = mapped_column(sa.String(200))
    dateiname_original: Mapped[str] = mapped_column(sa.String(255))
    pfad: Mapped[str] = mapped_column(sa.String(500))
    groesse_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    belegt_bytes: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    seitenzahl: Mapped[int] = mapped_column(sa.Integer)
    status: Mapped[str] = mapped_column(sa.String(20), default="neu")
    fehler_text: Mapped[str | None] = mapped_column(sa.String(500))
    dokument_gruppe_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("katplan_dokument.id", ondelete="SET NULL"))
    versionsnummer: Mapped[int] = mapped_column(sa.Integer, default=1)
    ist_aktuelle_version: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    herausgeber: Mapped[str] = mapped_column(sa.String(200), default="")
    stand: Mapped[date | None] = mapped_column(sa.Date)
    archiviert: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    hochgeladen_von_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"))
    hochgeladen_am: Mapped[datetime] = mapped_column(sa.DateTime, default=_utcnow)


class KatplanDokumentSeite(TenantScoped, Base):
    __tablename__ = "katplan_dokument_seite"
    __table_args__ = (
        sa.Index("ix_katplan_dokument_seite_org_plan", "org_id", "plan_id"),
        sa.Index("ix_katplan_dokument_seite_org_dok_nr", "org_id", "dokument_id", "seiten_nr"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    dokument_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("katplan_dokument.id", ondelete="CASCADE"))
    plan_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("katplan_plan.id", ondelete="CASCADE"))
    seiten_nr: Mapped[int] = mapped_column(sa.Integer)
    einzel_pdf_pfad: Mapped[str] = mapped_column(sa.String(500))
    bild_pfad: Mapped[str | None] = mapped_column(sa.String(500))
    thumb_pfad: Mapped[str | None] = mapped_column(sa.String(500))
    volltext: Mapped[str | None] = mapped_column(sa.Text)
    text_quelle: Mapped[str] = mapped_column(sa.String(10), default="none")
    titel: Mapped[str | None] = mapped_column(sa.String(200))
