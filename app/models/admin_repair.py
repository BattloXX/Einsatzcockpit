"""Globale Reparaturläufe und einmalige, aktionsgebundene Freigaben."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.admin_security import utcnow


class AdminRepairRun(Base):
    __tablename__ = "admin_repair_run"
    __table_args__ = (
        CheckConstraint("risiko IN ('low','medium','high','critical')", name="ck_repair_risiko"),
        CheckConstraint(
            "status IN ('vorgeschlagen','simuliert','freigegeben','laufend','erfolgreich',"
            "'fehlgeschlagen','abgebrochen')",
            name="ck_repair_status",
        ),
        CheckConstraint(
            "(status = 'laufend' AND active_aktion = aktion_id AND active_aktion IS NOT NULL) OR "
            "(status <> 'laufend' AND active_aktion IS NULL)",
            name="ck_repair_mutex",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aktion_id: Mapped[str] = mapped_column(String(80), nullable=False)
    diag_finding_id: Mapped[int | None] = mapped_column(ForeignKey("diag_finding.id", ondelete="SET NULL"))
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    risiko: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="vorgeschlagen")
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    angefordert_von: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"))
    simulation_hash: Mapped[str | None] = mapped_column(String(64))
    vorschau: Mapped[dict] = mapped_column(JSON, default=dict)
    ergebnis: Mapped[dict] = mapped_column(JSON, default=dict)
    verifikation: Mapped[dict] = mapped_column(JSON, default=dict)
    korrelations_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    simulated_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime().with_variant(DATETIME(fsp=6), "mysql", "mariadb"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Nullable UNIQUE ersetzt einen Host-Lock: genau ein laufender Lauf je Aktion.
    active_aktion: Mapped[str | None] = mapped_column(String(80), unique=True)


class AdminApproval(Base):
    __tablename__ = "admin_approval"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + INTERVAL 10 MINUTE", name="ck_approval_lifetime"
        ).ddl_if(callable_=lambda *args, **kw: kw["dialect"].name in ("mysql", "mariadb")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repair_run_id: Mapped[int] = mapped_column(ForeignKey("admin_repair_run.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    aktion_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
