"""Org-gebundener Suchindex und Warteschlange fuer Indexaenderungen."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SearchDocument(Base):
    __tablename__ = "search_document"
    __table_args__ = (
        sa.UniqueConstraint("org_id", "quelle", "quell_id", "scope_typ", "scope_id",
                            name="uq_search_document_quelle"),
        sa.Index("ix_search_document_org_quelle", "org_id", "quelle"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False,
    )
    quelle: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    quell_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    scope_typ: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="org", server_default="org")
    scope_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0, server_default="0")
    titel: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    untertitel: Mapped[str | None] = mapped_column(sa.String(255))
    search_keywords: Mapped[str | None] = mapped_column(sa.Text)
    sichtbarkeit: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="standard", server_default="standard",
    )
    lat: Mapped[float | None] = mapped_column(sa.Float)
    lng: Mapped[float | None] = mapped_column(sa.Float)
    meta_json: Mapped[str | None] = mapped_column(sa.Text)
    inhalt_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    quelle_geaendert_am: Mapped[datetime | None] = mapped_column(sa.DateTime)
    indexiert_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)

    chunks: Mapped[list[SearchChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="SearchChunk.chunk_nr",
    )


class SearchChunk(Base):
    __tablename__ = "search_chunk"
    __table_args__ = (
        sa.UniqueConstraint("document_id", "chunk_nr", name="uq_search_chunk_nr"),
        sa.Index("ix_search_chunk_org_doc", "org_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False,
    )
    document_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("search_document.id", ondelete="CASCADE"), nullable=False,
    )
    chunk_nr: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    seite: Mapped[int | None] = mapped_column(sa.Integer)
    abschnitt: Mapped[str | None] = mapped_column(sa.String(255))
    inhalt_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    document: Mapped[SearchDocument] = relationship(back_populates="chunks")


class SearchOutbox(Base):
    __tablename__ = "search_outbox"
    __table_args__ = (
        sa.Index("ix_search_outbox_faellig", "status", "naechster_versuch_am"),
        sa.Index("ix_search_outbox_quelle", "org_id", "quelle", "quell_id", "status"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False,
    )
    quelle: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    quell_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="pending", server_default="pending")
    versuche: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    naechster_versuch_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
    gesperrt_bis: Mapped[datetime | None] = mapped_column(sa.DateTime)
    letzter_fehler: Mapped[str | None] = mapped_column(sa.String(500))
    erstellt_am: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False, default=_utcnow)
