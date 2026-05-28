from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class LagekarteToken(Base):
    """Read-only Query-Token für den GeoJSON-Endpoint (/api/lagekarte/…).

    Scoped auf eine Organisation; optional zusätzlich auf einen einzelnen Einsatz.
    lagekarte.info trägt die URL mit ?token=<plain> ein – das Plain-Token wird
    einmalig angezeigt, danach nur noch der sha256-Hash gespeichert.
    """
    __tablename__ = "lagekarte_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    # Optional: Token gilt nur für diesen Einsatz (striktere Einschränkung)
    einsatz_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incident.id", ondelete="SET NULL"), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id])
    incident: Mapped[object | None] = relationship("Incident", foreign_keys=[einsatz_id])

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at < datetime.now(UTC):
            return False
        return True
