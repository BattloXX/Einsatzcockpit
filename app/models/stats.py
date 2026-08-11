from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StatistikDashboardToken(Base):
    __tablename__ = "statistik_dashboard_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
