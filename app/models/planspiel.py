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
