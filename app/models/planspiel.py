"""Datenmodell fuer Planspiele."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base

PLANSPIEL_STATUS = (
    "entwurf",
    "bereit",
    "laeuft",
    "pausiert",
    "generierung_gestoppt",
    "beendet",
    "abgebrochen",
)
PLANSPIEL_STATUS_LABELS: dict[str, str] = {
    "entwurf": "Entwurf",
    "bereit": "Bereit",
    "laeuft": "Läuft",
    "pausiert": "Pausiert",
    "generierung_gestoppt": "Generierung gestoppt",
    "beendet": "Beendet",
    "abgebrochen": "Abgebrochen",
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PlanspielRun(TenantScoped, Base):
    __tablename__ = "planspiel_run"
    __table_args__ = (sa.Index("ix_planspiel_run_org_status", "org_id", "status"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    titel: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    art: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    erzeugung: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False, default="entwurf")
    seed: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    tempo: Mapped[float] = mapped_column(sa.Float, nullable=False, default=1.0)
    konfig: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    szenario_snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    kanal_freigaben: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True, default=dict)
    leiter_user_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"))
    incident_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("incident.id", ondelete="SET NULL"))
    lage_id: Mapped[int | None] = mapped_column(sa.Integer, sa.ForeignKey("major_incident.id", ondelete="SET NULL"))
    gestartet_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    pausiert_seit: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    pausiert_summe_s: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    gen_aktiv: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    folge_aktiv: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    naechste_gen_sim_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    gen_seq: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    max_stellen: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    max_laufzeit_s: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    steuer_version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    worker_lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    letzter_fehler: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    beendet_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    created_by_user_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"))


class PlanspielVorlage(TenantScoped, Base):
    """Wiederverwendbare Konfiguration fuer einen neuen Planspiel-Entwurf."""

    __tablename__ = "planspiel_vorlage"
    __table_args__ = (sa.Index("ix_planspiel_vorlage_org_art", "org_id", "art"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    titel: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    beschreibung: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    art: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    konfig: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False)
    seed_fixiert: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    quelle_run_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("planspiel_run.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    zuletzt_verwendet_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    verwendet_anzahl: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)


class PlanspielRunEreignis(TenantScoped, Base):
    __tablename__ = "planspiel_run_ereignis"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "seq"),
        sa.Index("ix_planspiel_run_ereignis_run_status_faellig", "run_id", "status", "faellig_sim_s"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("planspiel_run.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    quelle: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    baustein_code: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    ziel_typ: Mapped[str | None] = mapped_column(sa.String(12), nullable=True)
    ziel_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    trigger_type: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    trigger_param: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    faellig_sim_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="geplant")
    idempotenz_key: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    versuche: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    ausgefuehrt_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    ausgefuehrt_sim_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    ergebnis_ref: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    fehler: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    sichtbarkeit: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="teilnehmer")
    text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class PlanspielZiel(TenantScoped, Base):
    __tablename__ = "planspiel_ziel"
    __table_args__ = (sa.UniqueConstraint("run_id", "seq"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("planspiel_run.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    typ: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    incident_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("incident.id", ondelete="SET NULL"))
    site_id: Mapped[int | None] = mapped_column(sa.Integer, sa.ForeignKey("incident_site.id", ondelete="SET NULL"))
    marker_id: Mapped[int | None] = mapped_column(
        sa.Integer, sa.ForeignKey("cross_site_marker.id", ondelete="SET NULL")
    )
    szenario_code: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    stichwort_code: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)
    adress_snapshot: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    ist_duplikat_von: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("planspiel_ziel.id", ondelete="SET NULL")
    )
    erzeugt_sim_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)


class PlanspielMitglied(TenantScoped, Base):
    __tablename__ = "planspiel_mitglied"
    __table_args__ = (sa.UniqueConstraint("run_id", "user_id"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("planspiel_run.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    rolle: Mapped[str] = mapped_column(sa.String(12), nullable=False)


class PlanspielBeobachtung(TenantScoped, Base):
    __tablename__ = "planspiel_beobachtung"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("planspiel_run.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"))
    sim_s: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    lernziel: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    kategorie: Mapped[str | None] = mapped_column(sa.String(40), nullable=True)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    bewertung: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)


class PlanspielSzenario(TenantScoped, Base):
    __tablename__ = "planspiel_szenario"
    __table_args__ = (sa.UniqueConstraint("org_id", "code", name="uq_planspiel_szenario_org_code"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    herkunft: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    system_code: Mapped[str | None] = mapped_column(sa.String(40))
    system_version: Mapped[int | None] = mapped_column(sa.Integer)
    org_geaendert: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    aktiv: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    zufall_erlaubt: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    gewicht: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=10)
    aktive_version_id: Mapped[int | None] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey(
            "planspiel_szenario_version.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_planspiel_szenario_aktive_version",
        ),
    )
    archiviert_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    created_by_user_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"))
    updated_by_user_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"))


class PlanspielSzenarioVersion(TenantScoped, Base):
    __tablename__ = "planspiel_szenario_version"
    __table_args__ = (sa.UniqueConstraint("szenario_id", "version", name="uq_planspiel_szenario_version"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    szenario_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("planspiel_szenario.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    veroeffentlicht_at: Mapped[datetime | None] = mapped_column(sa.DateTime)
    titel: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    beschreibung: Mapped[str | None] = mapped_column(sa.Text)
    kategorie: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    stichwort_code: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    stichwort_quelle: Mapped[str | None] = mapped_column(sa.String(60))
    alarmtext_template: Mapped[str] = mapped_column(sa.Text, nullable=False)
    regie_geheimlage: Mapped[str | None] = mapped_column(sa.Text)
    grundsituation: Mapped[str | None] = mapped_column(sa.Text)
    ort_typen: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False)
    geografie_bedingung: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    requires_existing_object: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    requires_verified_bma: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    min_adressqualitaet: Mapped[str | None] = mapped_column(sa.String(1))
    schwierigkeit: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    schlagworte: Mapped[str | None] = mapped_column(sa.Text)
    lernziele: Mapped[str | None] = mapped_column(sa.Text)
    ressourcenhinweis: Mapped[str | None] = mapped_column(sa.Text)
    ausschlussregeln: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    gsl_modus: Mapped[str | None] = mapped_column(sa.String(20))
    gsl_verteilung: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    created_by_user_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"))


class PlanspielSzenarioEreignis(TenantScoped, Base):
    __tablename__ = "planspiel_szenario_ereignis"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("planspiel_szenario_version.id", ondelete="CASCADE"), nullable=False
    )
    baustein_code: Mapped[str | None] = mapped_column(sa.String(40))
    ereignis_typ: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    trigger_type: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    offset_min_s: Mapped[int | None] = mapped_column(sa.Integer)
    offset_max_s: Mapped[int | None] = mapped_column(sa.Integer)
    trigger_param: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    wahrscheinlichkeit: Mapped[float] = mapped_column(sa.Float, nullable=False, default=1.0)
    max_fires: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    prioritaet: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    sichtbarkeit: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="teilnehmer")
    ziel: Mapped[str] = mapped_column(sa.String(12), nullable=False, default="stelle")
    payload_template: Mapped[str] = mapped_column(sa.Text, nullable=False)
    voraussetzungen: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    abbruch: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    reihenfolge: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)


class PlanspielOrtTypZuordnung(TenantScoped, Base):
    __tablename__ = "planspiel_ort_typ_zuordnung"
    __table_args__ = (
        sa.UniqueConstraint("org_id", "quelle", "schluessel", "ort_typ", name="uq_planspiel_ort_typ_zuordnung"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    quelle: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    schluessel: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    ort_typ: Mapped[str] = mapped_column(sa.String(40), nullable=False)


class PlanspielAdresse(TenantScoped, Base):
    """Wiederverwendbarer, organisationsgebundener Ort fuer Planspiele."""
    __tablename__ = "planspiel_adresse"
    __table_args__ = (
        sa.UniqueConstraint("org_id", "quelle", "quell_id", name="uq_planspiel_adresse_org_quelle_quell_id"),
        sa.Index("ix_planspiel_adresse_org_aktiv", "org_id", "aktiv"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    quelle: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    quell_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    objekt_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("objekt.id", ondelete="SET NULL"), nullable=True
    )
    bezeichnung: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    strasse: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    hausnr: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    plz: Mapped[str | None] = mapped_column(sa.String(10), nullable=True)
    ort: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    lat: Mapped[float] = mapped_column(sa.Float, nullable=False)
    lng: Mapped[float] = mapped_column(sa.Float, nullable=False)
    qualitaet: Mapped[str] = mapped_column(sa.String(1), nullable=False)
    ort_typen: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False)
    merkmale: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON, nullable=True)
    aktiv: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    zuletzt_verwendet_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
