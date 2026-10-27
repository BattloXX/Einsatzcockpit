"""Globale, kurzlebige Diagnoseergebnisse ohne fachliche Nutzdaten."""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.admin_security import utcnow


class DiagRun(Base):
    __tablename__ = "diag_run"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','done','failed','cancelled')", name="ck_diag_run_status"),
        CheckConstraint("quelle IN ('manual','mcp','schedule')", name="ck_diag_run_quelle"),
        CheckConstraint("active_slot IS NULL OR active_slot = 1", name="ck_diag_run_slot"),
        CheckConstraint("(status IN ('queued','running') AND active_slot IS NOT NULL AND active_slot = 1) OR "
                        "(status IN ('done','failed','cancelled') AND active_slot IS NULL)",
                        name="ck_diag_run_active"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    umfang: Mapped[dict] = mapped_column(JSON, default=dict)
    checks: Mapped[list] = mapped_column(JSON, default=list)
    angefordert_von: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    quelle: Mapped[str] = mapped_column(String(16), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    fortschritt_am: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    fortschritt: Mapped[dict] = mapped_column(JSON, default=dict)
    fehlertext: Mapped[str | None] = mapped_column(Text)
    korrelations_id: Mapped[str] = mapped_column(String(36), unique=True)
    # DB-weite Serialisierung auch zwischen Hosts; terminale Laeufe geben den Slot frei.
    active_slot: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)


class DiagFinding(Base):
    __tablename__ = "diag_finding"
    __table_args__ = (
        CheckConstraint("status IN ('offen','behoben','unterdrueckt')", name="ck_diag_finding_status"),
        CheckConstraint("schweregrad IN ('info','warn','fail','kritisch')", name="ck_diag_finding_schwere"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("diag_run.id", ondelete="CASCADE"), index=True)
    finding_key: Mapped[str] = mapped_column(String(64), unique=True)
    check_id: Mapped[str] = mapped_column(String(80), index=True)
    modul: Mapped[str] = mapped_column(String(40), index=True)
    schweregrad: Mapped[str] = mapped_column(String(10))
    titel: Mapped[str] = mapped_column(String(200))
    evidenz: Mapped[dict] = mapped_column(JSON, default=dict)
    betroffene_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    ursache_hypothese: Mapped[str] = mapped_column(String(500))
    empfehlung: Mapped[str] = mapped_column(String(500))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    behoben_am: Mapped[datetime | None] = mapped_column(DateTime)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="offen", index=True)
