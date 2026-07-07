"""Bild-Annotation (nicht-destruktiv) fuer alle Media-Typen.

Gemeinsame, polymorphe Tabelle statt Spalten in jeder der sechs Media-Tabellen
(task_media, message_media, person_media, site_media, cross_marker_media,
lage_journal_media). `media_typ` diskriminiert, `media_id` referenziert die
jeweilige Media-Zeile (keine echte FK, da polymorph). Zugriff/Scoping laeuft
immer ueber die Host-Entitaet; `org_id` wird zusaetzlich denormalisiert gefuehrt.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Erlaubte media_typ-Werte (Diskriminator). Muss mit dem Resolver in
# app/services/annotation_service.py uebereinstimmen.
MEDIA_TYPEN = ("task", "message", "person", "site", "cross_marker", "lage_journal")


class MediaAnnotation(Base):
    """Eine Zeile je annotierbarem Bild. Traegt Vektordaten, flaches PNG,
    Soft-Lock und die Herkunft bei Objektuebernahme."""
    __tablename__ = "media_annotation"
    __table_args__ = (
        UniqueConstraint("media_typ", "media_id", name="uq_media_annotation_target"),
        Index("ix_media_annotation_org", "org_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    media_typ: Mapped[str] = mapped_column(String(16), nullable=False)
    media_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Konva stage.toJSON() des Annotation-Layers (Vektordaten, editierbar)
    annotation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pfad zum flachen PNG (im selben Media-Storage neben dem Original)
    annotated_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    annotated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    annotated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Soft-Lock (Heartbeat, TTL 5 min) — im Einsatz nie hart blockieren
    locked_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Herkunft bei Uebernahme aus der Objektverwaltung (auch ohne Annotation gesetzt)
    source_objekt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_dokument_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_seite: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MediaAnnotationVersion(Base):
    """Leichtgewichtiges Archiv des vorherigen annotation_json bei jedem Save
    (Einsatzdokumentation: Nachvollziehbarkeit, wer wann was eingezeichnet hat)."""
    __tablename__ = "media_annotation_version"
    __table_args__ = (Index("ix_media_annotation_version_ann", "annotation_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    annotation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    annotation_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
