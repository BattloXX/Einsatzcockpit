"""Datenmodell: Atemschutzgeräteprüfung (Prüfprotokoll je Pressluftatmer)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.master import Member

EINSATZ_ARTEN = ["uebung", "einsatz"]
ERFASSUNGSWEGE = ["public", "intern"]


class AtemschutzGeraet(TenantScoped, Base):
    """Stammdaten: Atemschutzgerät (Pressluftatmer) je Org."""
    __tablename__ = "atemschutz_geraet"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # org_id via TenantScoped
    nummer: Mapped[str] = mapped_column(String(50), nullable=False)
    bezeichnung: Mapped[str | None] = mapped_column(String(200), nullable=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    @property
    def anzeige_label(self) -> str:
        return f"{self.nummer} – {self.bezeichnung}" if self.bezeichnung else self.nummer


class AtemschutzPruefung(Base):
    """Einzelnes Prüfprotokoll (Atemschutzgeräteprüfung).

    Drucküberprüfung nach dem Gebrauch (3 Grenzwerte, automatisch ausgewertet
    aus den erfassten BAR-Werten statt manueller i.O./n.i.O.-Angabe):
    Flaschendruck min. FLASCHENDRUCK_MIN_BAR, Hochdruck-Dichtprüfung max.
    HOCHDRUCK_DICHTPRUEFUNG_MAX_BAR_MIN BAR/min, Warnsignal-Prüfung zwischen
    WARNSIGNAL_MIN_BAR und WARNSIGNAL_MAX_BAR.
    """
    __tablename__ = "atemschutz_pruefung"

    FLASCHENDRUCK_MIN_BAR = 270
    HOCHDRUCK_DICHTPRUEFUNG_MAX_BAR_MIN = 10
    WARNSIGNAL_MIN_BAR = 50
    WARNSIGNAL_MAX_BAR = 60

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False, index=True
    )
    geraet_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("atemschutz_geraet.id", ondelete="RESTRICT"), nullable=False
    )
    traeger_member_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("member.id", ondelete="SET NULL"), nullable=True
    )
    traeger_free_text: Mapped[str | None] = mapped_column(String(200), nullable=True)

    eingesetzt_am: Mapped[date] = mapped_column(Date, nullable=False)
    ort_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    einsatz_art: Mapped[str] = mapped_column(String(20), nullable=False, default="uebung")
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident.id", ondelete="SET NULL"), nullable=True
    )

    flasche_gewechselt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    flaschendruck_bar: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sichtpruefung_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    druckabfall_bar: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hochdruckpruefung_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rueckzugssignal_bar: Mapped[int | None] = mapped_column(Integer, nullable=True)
    geraet_einsatzbereit_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    defekt_info: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_via: Mapped[str] = mapped_column(String(10), nullable=False, default="public")
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    geraet: Mapped[AtemschutzGeraet] = relationship(foreign_keys=[geraet_id], lazy="joined")
    traeger_member: Mapped[Member | None] = relationship(lazy="joined")  # type: ignore[name-defined]
    incident: Mapped[Incident | None] = relationship(back_populates="atemschutz_pruefungen")  # type: ignore[name-defined]
    benachrichtigungen: Mapped[list[AtemschutzPruefBenachrichtigung]] = relationship(
        back_populates="pruefung", cascade="all, delete-orphan"
    )

    @property
    def traeger_name(self) -> str:
        if self.traeger_member:
            return self.traeger_member.full_name
        return self.traeger_free_text or "Unbekannt"

    @property
    def flaschendruck_ok(self) -> bool:
        """True wenn kein Wert erfasst (Altdaten) oder Wert >= Grenzwert."""
        if self.flaschendruck_bar is None:
            return True
        return self.flaschendruck_bar >= self.FLASCHENDRUCK_MIN_BAR

    @property
    def warnsignal_ok(self) -> bool:
        """True wenn kein Wert erfasst (Altdaten) oder Wert im Sollbereich."""
        if self.rueckzugssignal_bar is None:
            return True
        return self.WARNSIGNAL_MIN_BAR <= self.rueckzugssignal_bar <= self.WARNSIGNAL_MAX_BAR

    @property
    def alles_ok(self) -> bool:
        return bool(
            self.flaschendruck_ok
            and self.sichtpruefung_ok
            and self.hochdruckpruefung_ok
            and self.warnsignal_ok
            and self.geraet_einsatzbereit_ok
        )

    @property
    def defekte_punkte(self) -> list[str]:
        punkte = []
        if not self.flaschendruck_ok:
            punkte.append("Flaschendruck")
        if not self.sichtpruefung_ok:
            punkte.append("Sichtprüfung")
        if not self.hochdruckpruefung_ok:
            punkte.append("Hochdruck-Dichtprüfung")
        if not self.warnsignal_ok:
            punkte.append("Warnsignal-Prüfung")
        if not self.geraet_einsatzbereit_ok:
            punkte.append("Gerät einsatzbereit")
        return punkte


class AtemschutzPruefBenachrichtigung(Base):
    """Audit/Retry der Wart-Benachrichtigung bei Defekt (Mail & Teams)."""
    __tablename__ = "atemschutz_pruef_benachrichtigung"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pruefung_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("atemschutz_pruefung.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False)
    kanal: Mapped[str] = mapped_column(String(10), nullable=False)  # "mail" | "teams"
    empfaenger: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # "gesendet" | "fehler"
    fehlertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    gesendet_am: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    pruefung: Mapped[AtemschutzPruefung] = relationship(back_populates="benachrichtigungen")


# Forward reference resolution (analog app/models/breathing.py)
from app.models.incident import Incident  # noqa: E402, F401
