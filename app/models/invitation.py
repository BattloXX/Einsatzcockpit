"""Org-übergreifende Einsatz-Einladungen und Partner-Konfiguration."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OrgInvitation(Base):
    """Einladung einer Org zu einem fremden Einsatz."""
    __tablename__ = "org_invitation"
    __table_args__ = (
        UniqueConstraint("incident_id", "invited_org_id", name="uq_org_invitation_incident_org"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inviting_org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False
    )
    invited_org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # pending | accepted | declined | revoked
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    inviting_org: Mapped[object] = relationship(
        "FireDept", foreign_keys=[inviting_org_id], lazy="joined"
    )
    invited_org: Mapped[object] = relationship(
        "FireDept", foreign_keys=[invited_org_id], lazy="joined"
    )


class OrgPartner(Base):
    """Konfigurierte Nachbar-/Partner-Orgs: bei notify_neighbors-Einsätzen werden
    Einladungsvorschläge an alle Partner dieser Org erstellt."""
    __tablename__ = "org_partner"

    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), primary_key=True
    )
    partner_org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), primary_key=True
    )
    # True = Einladungsvorschlag bei notify_neighbors-Alarm
    notify_on_incident: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id])
    partner_org: Mapped[object] = relationship("FireDept", foreign_keys=[partner_org_id])
