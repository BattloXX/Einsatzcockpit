"""Globale DB-Claims fuer Tagesdiagnose und geheimnisfreie Meldungsmetadaten."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DiagScheduleSlot(Base):
    __tablename__ = "diag_schedule_slot"
    slot: Mapped[str] = mapped_column(String(10), primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("diag_run.id", ondelete="SET NULL"))


class DiagNotification(Base):
    __tablename__ = "diag_notification"
    finding_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    count: Mapped[int] = mapped_column(Integer, default=0)
