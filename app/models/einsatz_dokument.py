"""Unveränderliche, je Organisation deduplizierte Einsatzfassungen."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.tenant import TenantScoped
from app.db import Base


class EinsatzDokumentFassung(TenantScoped, Base):
    __tablename__ = "einsatz_dokument_fassung"
    __table_args__ = (sa.UniqueConstraint("org_id", "sha256", name="uq_einsatz_fassung_hash"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    # Transaktional, in bestehenden DB-Backups/Organisationsexporten enthalten.
    inhalt: Mapped[bytes] = mapped_column(sa.LargeBinary().with_variant(LONGBLOB(), "mysql", "mariadb"), deferred=True)


class EinsatzDokument(TenantScoped, Base):
    __tablename__ = "einsatz_dokument"
    __table_args__ = (
        sa.CheckConstraint("(incident_id IS NOT NULL AND lage_id IS NULL) OR "
                           "(incident_id IS NULL AND lage_id IS NOT NULL)", name="ck_einsatz_dokument_ziel"),
        sa.Index("ix_einsatz_dokument_incident", "incident_id"),
        sa.Index("ix_einsatz_dokument_lage", "lage_id"),
    )

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("incident.id", ondelete="CASCADE"))
    lage_id: Mapped[int | None] = mapped_column(sa.Integer, sa.ForeignKey("major_incident.id", ondelete="CASCADE"))
    fassung_id: Mapped[int] = mapped_column(sa.Integer, sa.ForeignKey("einsatz_dokument_fassung.id"))
    titel: Mapped[str] = mapped_column(sa.String(300))
    quell_url: Mapped[str] = mapped_column(sa.String(2048))
    quelle_json: Mapped[str] = mapped_column(sa.Text)
    seiten: Mapped[str] = mapped_column(sa.Text)
    seitenzahl: Mapped[int] = mapped_column(sa.Integer)
    vertraulich: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    uebernommen_am: Mapped[datetime] = mapped_column(
        sa.DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    uebernommen_von_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("user.id", ondelete="SET NULL"))
    task_id: Mapped[int | None] = mapped_column(sa.BigInteger, sa.ForeignKey("task.id", ondelete="SET NULL"))
    site_id: Mapped[int | None] = mapped_column(sa.Integer, sa.ForeignKey("incident_site.id", ondelete="SET NULL"))
    auftrag_freigegeben: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    entfernt_am: Mapped[datetime | None] = mapped_column(sa.DateTime)
    revision: Mapped[int] = mapped_column(sa.Integer, default=1)
    empfaenger: Mapped[list[EinsatzDokumentEmpfaenger]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class EinsatzDokumentEmpfaenger(TenantScoped, Base):
    __tablename__ = "einsatz_dokument_empfaenger"
    __table_args__ = (sa.UniqueConstraint("dokument_id", "einheit_id", name="uq_einsatz_dokument_empfaenger"),)

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    dokument_id: Mapped[int] = mapped_column(sa.Integer, sa.ForeignKey("einsatz_dokument.id", ondelete="CASCADE"))
    einheit_id: Mapped[int] = mapped_column(sa.Integer, sa.ForeignKey("lage_einheit.id", ondelete="CASCADE"))


@sa.event.listens_for(EinsatzDokumentFassung, "before_update")
def _unveraenderlich(mapper, connection, target):
    raise RuntimeError("Einsatzfassungen sind unveränderlich")


@sa.event.listens_for(EinsatzDokument, "before_update")
def _provenienz_unveraenderlich(mapper, connection, target):
    state = sa.inspect(target)
    for name in ("fassung_id", "titel", "quell_url", "quelle_json", "seiten", "seitenzahl", "vertraulich",
                 "incident_id", "lage_id", "uebernommen_am", "uebernommen_von_id", "org_id"):
        if state.attrs[name].history.has_changes():
            raise RuntimeError("Die Provenienz einer Einsatzfassung ist unveränderlich")
