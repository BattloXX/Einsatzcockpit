"""Globale Prozessdiagnose; enthaelt keine mandantenbezogenen Nutzdaten."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WorkerRuntime(Base):
    __tablename__ = "worker_runtime"

    worker_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(64), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_leader: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    leader_since: Mapped[datetime | None] = mapped_column(DateTime)
    leader_error: Mapped[str | None] = mapped_column(String(40))
    redis: Mapped[dict] = mapped_column(JSON, nullable=False)
    loops: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (Index("ix_worker_runtime_expiry", "expires_at"),)
