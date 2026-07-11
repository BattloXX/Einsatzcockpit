"""SMS-PIN-Login: passwortlose Anmeldung per Einmal-PIN an die hinterlegte Telefonnummer.

Alternative zur normalen Benutzername/Passwort-Anmeldung, v.a. für die Android-App
(neben QR-Code-Scan und normalem Login). Mirror-Muster von PasswordResetToken.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.user import User

# Ein PIN ist 10 Minuten gueltig und erlaubt maximal 5 Fehlversuche.
LOGIN_PIN_TTL_MINUTES = 10
LOGIN_PIN_MAX_ATTEMPTS = 5


class LoginPin(Base):
    __tablename__ = "login_pin"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    # sha256-Hex des PIN (kein Klartext-PIN in der DB) — Schutz gegen Bruteforce
    # laeuft ueber Rate-Limit + attempt_count + kurze Gueltigkeit, nicht ueber
    # die Hash-Kosten (ein 6-stelliger PIN hat ohnehin nur 10^6 Kombinationen).
    pin_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requesting_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship("User")

    @property
    def is_valid(self) -> bool:
        if self.used_at is not None:
            return False
        if self.attempt_count >= LOGIN_PIN_MAX_ATTEMPTS:
            return False
        now = datetime.now(UTC)
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return exp > now
