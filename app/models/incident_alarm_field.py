"""Herkunft der durch Alarmnachrichten gepflegten Einsatzfelder."""
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenant import TenantScoped
from app.db import Base


class IncidentAlarmField(TenantScoped, Base):
    __tablename__ = "incident_alarm_field"
    __table_args__ = (UniqueConstraint("incident_id", "field", name="uq_incident_alarm_field"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(String(40))
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(10))
    ingest_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alarm_ingest.id", ondelete="SET NULL"), nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None),
    )
