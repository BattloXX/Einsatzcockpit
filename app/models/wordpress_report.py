from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class WordPressReportConfig(Base):
    """WordPress-Berichtskonfiguration einer Organisation (1:1 je Org)."""

    __tablename__ = "wordpress_report_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    webhook_token: Mapped[str | None] = mapped_column(String(200), nullable=True)

    org: Mapped[object] = relationship("FireDept", foreign_keys=[org_id], lazy="joined")
